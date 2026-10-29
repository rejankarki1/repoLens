"""Groq generation client and provider selection, against a mocked provider."""

import json

import httpx
import pytest

from apps.api.config import Settings as _Settings
from apps.api.services import generation
from apps.api.services.generation import Finished, GenerationError, TextDelta
from apps.api.services.groq_generation import GroqGenerator

pytestmark = pytest.mark.anyio


def Settings(**values) -> _Settings:  # noqa: N802
    """Settings that ignore the developer's real .env, so these tests do not depend on it."""
    return _Settings(_env_file=None, **values)

KEY = "test-groq-key-not-a-real-secret"
CONTENTS = [
    {"role": "user", "parts": [{"text": "first"}]},
    {"role": "model", "parts": [{"text": "answer"}]},
    {"role": "user", "parts": [{"text": "follow"}, {"text": " up"}]},
]


def make(handler, **overrides) -> GroqGenerator:
    settings = Settings(groq_api_key=KEY, **overrides)

    async def no_sleep(_: float) -> None:
        return None

    return GroqGenerator(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), sleep=no_sleep)


def reply(text: str, reason: str = "stop") -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": reason}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 2}}


def sse(*chunks: dict | str) -> bytes:
    return "".join(f"data: {c if isinstance(c, str) else json.dumps(c)}\n\n" for c in chunks).encode()


def delta(text: str, reason: str | None = None) -> dict:
    return {"choices": [{"delta": {"content": text}, "finish_reason": reason}]}


async def test_generate_request_shape_and_key_only_in_header() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(headers=request.headers, url=str(request.url), body=json.loads(request.content))
        return httpx.Response(200, json=reply("hello"))

    result = await make(handler, groq_generation_model="m-1", groq_reasoning_effort="low").generate("be brief", CONTENTS)
    assert result.text == "hello" and result.prompt_tokens == 7 and result.output_tokens == 2
    assert seen["headers"]["authorization"] == f"Bearer {KEY}" and KEY not in seen["url"]
    assert seen["url"].endswith("/openai/v1/chat/completions")
    body = seen["body"]
    assert body["model"] == "m-1" and body["stream"] is False and body["reasoning_effort"] == "low"
    assert [(m["role"], m["content"]) for m in body["messages"]] == [
        ("system", "be brief"), ("user", "first"), ("assistant", "answer"), ("user", "follow up"),
    ]


async def test_reasoning_effort_is_omitted_when_unset() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=reply("x"))

    await make(handler).generate("s", CONTENTS)
    assert "reasoning_effort" not in seen["body"]


@pytest.mark.parametrize(
    ("status", "code"), [(429, "rate_limited"), (401, "auth"), (403, "auth"), (404, "bad_request"), (413, "bad_request"), (500, "unavailable"), (400, "bad_request")]
)
async def test_http_errors_map_to_codes_without_echoing_provider_text(status: int, code: str) -> None:
    gen = make(lambda r: httpx.Response(status, json={"error": {"message": f"secret-detail {KEY}"}}))
    with pytest.raises(GenerationError) as info:
        await gen.generate("s", CONTENTS, attempts=1)
    assert info.value.code == code
    assert "secret-detail" not in info.value.message and KEY not in info.value.message


async def test_rate_limit_carries_retry_after_and_is_not_retried() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "23"}, json={"error": {"message": "tpm"}})

    with pytest.raises(GenerationError) as info:
        await make(handler).generate("s", CONTENTS)
    assert info.value.code == "rate_limited" and info.value.retry_after == 23 and len(calls) == 1


async def test_server_error_is_retried_once() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, json=reply("ok"))

    assert (await make(handler).generate("s", CONTENTS)).text == "ok" and len(calls) == 2


async def test_timeout_maps_to_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(GenerationError) as info:
        await make(handler).generate("s", CONTENTS, attempts=1)
    assert info.value.code == "timeout"


@pytest.mark.parametrize(("reason", "code"), [("length", "truncated"), ("content_filter", "blocked"), (None, "interrupted")])
async def test_incomplete_finish_is_an_error(reason: str | None, code: str) -> None:
    gen = make(lambda r: httpx.Response(200, json=reply("partial", reason)))  # type: ignore[arg-type]
    with pytest.raises(GenerationError) as info:
        await gen.generate("s", CONTENTS, attempts=1)
    assert info.value.code == code


async def test_empty_answer_is_an_error() -> None:
    gen = make(lambda r: httpx.Response(200, json=reply("  ")))
    with pytest.raises(GenerationError) as info:
        await gen.generate("s", CONTENTS, attempts=1)
    assert info.value.code == "empty"


async def test_stream_yields_deltas_then_finished_and_ignores_reasoning() -> None:
    reasoning = {"choices": [{"delta": {"reasoning": "thinking..."}, "finish_reason": None}]}
    body = sse(reasoning, delta("Hel"), delta("lo"), delta("", "stop"), "[DONE]")
    gen = make(lambda r: httpx.Response(200, content=body, headers={"content-type": "text/event-stream"}))
    events = [e async for e in gen.stream("s", CONTENTS)]
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hel", "lo"]
    assert isinstance(events[-1], Finished)


async def test_stream_request_asks_for_streaming() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=sse(delta("a", "stop"), "[DONE]"))

    [_ async for _ in make(handler).stream("s", CONTENTS)]
    assert seen["body"]["stream"] is True


async def test_stream_cut_off_by_length_raises_after_partial_text() -> None:
    gen = make(lambda r: httpx.Response(200, content=sse(delta("part"), delta("", "length"), "[DONE]")))
    seen = []
    with pytest.raises(GenerationError) as info:
        async for event in gen.stream("s", CONTENTS):
            seen.append(event)
    assert info.value.code == "truncated" and not any(isinstance(e, Finished) for e in seen)


async def test_stream_ending_without_finish_reason_is_interrupted() -> None:
    gen = make(lambda r: httpx.Response(200, content=sse(delta("part"))))
    with pytest.raises(GenerationError) as info:
        async for _ in gen.stream("s", CONTENTS):
            pass
    assert info.value.code == "interrupted"


async def test_stream_http_error_and_rate_limit_header() -> None:
    gen = make(lambda r: httpx.Response(429, headers={"retry-after": "7"}))
    with pytest.raises(GenerationError) as info:
        async for _ in gen.stream("s", CONTENTS):
            pass
    assert info.value.code == "rate_limited" and info.value.retry_after == 7


@pytest.mark.parametrize(("kind", "expected"), [("rate_limit_exceeded", "rate_limited"), ("server_error", "unavailable")])
async def test_stream_error_chunk_after_partial_text(kind: str, expected: str) -> None:
    error = {"error": {"message": "secret-detail", "type": kind}}
    gen = make(lambda r: httpx.Response(200, content=sse(delta("part"), error)))
    seen = []
    with pytest.raises(GenerationError) as info:
        async for event in gen.stream("s", CONTENTS):
            seen.append(event)
    assert info.value.code == expected and "secret-detail" not in info.value.message
    assert not any(isinstance(e, Finished) for e in seen)


async def test_missing_key_is_not_configured() -> None:
    with pytest.raises(GenerationError) as info:
        GroqGenerator(Settings(groq_api_key=None))
    assert info.value.code == "not_configured"


# --- provider selection ------------------------------------------------------------------------------


@pytest.fixture
def select(monkeypatch: pytest.MonkeyPatch):
    def apply(**values):
        settings = Settings(**values)
        monkeypatch.setattr(generation, "get_settings", lambda: settings)
        monkeypatch.setattr("apps.api.services.groq_generation.get_settings", lambda: settings)
        generation._shared_generator.cache_clear()
        return generation.get_generator()

    yield apply
    generation._shared_generator.cache_clear()


def test_default_provider_is_gemini(select) -> None:
    gen = select(gemini_api_key="g-key", groq_api_key="q-key")
    assert gen.provider == "gemini" and gen.model == Settings().gemini_generation_model


def test_groq_selected_by_env_setting(select) -> None:
    gen = select(generation_provider="groq", gemini_api_key="g-key", groq_api_key="q-key", groq_generation_model="m-2")
    assert gen.provider == "groq" and gen.model == "m-2"


def test_groq_works_without_a_gemini_key(select) -> None:
    assert select(generation_provider="groq", gemini_api_key=None, groq_api_key="q-key").provider == "groq"


def test_no_automatic_fallback_to_the_other_provider(select) -> None:
    assert select(generation_provider="groq", gemini_api_key="g-key", groq_api_key=None) is None
    assert select(generation_provider="gemini", gemini_api_key=None, groq_api_key="q-key") is None


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(generation_provider="openai")  # type: ignore[arg-type]
