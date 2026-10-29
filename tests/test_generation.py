"""Gemini generation client: request shape, errors and streaming, against a mocked provider."""

import json

import httpx
import pytest

from apps.api.config import Settings
from apps.api.services.generation import Finished, GeminiGenerator, GenerationError, TextDelta

pytestmark = pytest.mark.anyio

KEY = "test-key-not-a-real-secret"
CONTENTS = [{"role": "user", "parts": [{"text": "hi"}]}]


def make(handler, **overrides) -> GeminiGenerator:
    settings = Settings(_env_file=None, gemini_api_key=KEY, **overrides)

    async def no_sleep(_: float) -> None:
        return None

    return GeminiGenerator(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), sleep=no_sleep)


def reply(text: str, reason: str = "STOP") -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": reason}],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3}}


async def test_generate_sends_key_in_header_and_returns_text() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"], seen["url"], seen["body"] = request.headers, str(request.url), json.loads(request.content)
        return httpx.Response(200, json=reply("answer"))

    result = await make(handler).generate("be brief", CONTENTS)
    assert result.text == "answer" and result.output_tokens == 3
    assert seen["headers"]["x-goog-api-key"] == KEY and KEY not in seen["url"]
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "be brief"
    assert seen["body"]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}


@pytest.mark.parametrize(
    ("status", "code"), [(429, "rate_limited"), (401, "auth"), (403, "auth"), (500, "unavailable"), (400, "bad_request")]
)
async def test_http_errors_map_to_codes_without_echoing_provider_text(status: int, code: str) -> None:
    gen = make(lambda r: httpx.Response(status, json={"error": {"message": f"secret-detail {KEY}"}}))
    with pytest.raises(GenerationError) as info:
        await gen.generate("s", CONTENTS, attempts=1)
    assert info.value.code == code
    assert "secret-detail" not in info.value.message and KEY not in info.value.message


async def test_retries_transient_failure_once() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, json=reply("ok"))

    assert (await make(handler).generate("s", CONTENTS)).text == "ok"
    assert len(calls) == 2


@pytest.mark.parametrize("reason", ["MAX_TOKENS", "SAFETY", "RECITATION"])
async def test_incomplete_finish_reasons_are_errors(reason: str) -> None:
    gen = make(lambda r: httpx.Response(200, json=reply("partial", reason)))
    with pytest.raises(GenerationError):
        await gen.generate("s", CONTENTS, attempts=1)


async def test_blocked_prompt_is_an_error() -> None:
    gen = make(lambda r: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}))
    with pytest.raises(GenerationError):
        await gen.generate("s", CONTENTS, attempts=1)


def sse(*chunks: dict) -> bytes:
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks).encode()


async def test_stream_yields_deltas_then_finished() -> None:
    body = sse(reply("Hel", ""), reply("lo", "STOP"))
    gen = make(lambda r: httpx.Response(200, content=body, headers={"content-type": "text/event-stream"}))
    events = [e async for e in gen.stream("s", CONTENTS)]
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hel", "lo"]
    assert isinstance(events[-1], Finished)


async def test_stream_truncated_by_max_tokens_raises_after_partial_text() -> None:
    body = sse(reply("part", "MAX_TOKENS"))
    gen = make(lambda r: httpx.Response(200, content=body))
    seen = []
    with pytest.raises(GenerationError):
        async for event in gen.stream("s", CONTENTS):
            seen.append(event)
    assert not any(isinstance(e, Finished) for e in seen)


async def test_stream_http_error() -> None:
    gen = make(lambda r: httpx.Response(429, headers={"retry-after": "7"}))
    with pytest.raises(GenerationError) as info:
        async for _ in gen.stream("s", CONTENTS):
            pass
    assert info.value.code == "rate_limited" and info.value.retry_after == 7


@pytest.mark.parametrize(("code", "expected"), [(503, "unavailable"), (429, "rate_limited")])
async def test_stream_error_chunk_after_partial_text_is_a_provider_error(code: int, expected: str) -> None:
    error = {"error": {"code": code, "message": "high demand secret-detail", "status": "UNAVAILABLE"}}
    gen = make(lambda r: httpx.Response(200, content=sse(reply("part", ""), error)))
    seen = []
    with pytest.raises(GenerationError) as info:
        async for event in gen.stream("s", CONTENTS):
            seen.append(event)
    assert info.value.code == expected and "secret-detail" not in info.value.message
    assert not any(isinstance(e, Finished) for e in seen)
