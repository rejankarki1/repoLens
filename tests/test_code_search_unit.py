"""Unit tests for search text normalization, query building, fusion and the embedding provider client.

No database and no network: the provider is exercised against an httpx mock transport.
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from apps.api.config import Settings
from apps.api.services.code_search import (
    SEARCH_VECTOR_BACKFILL_SQL,
    build_tsquery,
    exact_key,
    reciprocal_rank_fusion,
    search_documents,
    split_camel,
)
from apps.api.models.entities import EMBEDDING_DIMENSIONS
from apps.api.services.embeddings import (
    DOCUMENT_TASK,
    MAX_BATCH_SIZE,
    QUERY_TASK,
    EmbeddingNotConfiguredError,
    EmbeddingProviderError,
    GeminiEmbedder,
    model_key,
)

DIMS = 1_536


# --- normalization and query building -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "split"),
    [("loadEnvLocal", "load Env Local"), ("HTTPResponse2", "HTTP Response2"), ("getUserByID", "get User By ID"),
     ("snake_case", "snake_case"), ("plain", "plain")],
)
def test_split_camel(text: str, split: str) -> None:
    assert split_camel(text) == split


def test_search_documents_strip_punctuation_and_add_camel_parts() -> None:
    meta, body = search_documents("src/components/AppButton.tsx", ["Props", "AppButton"], "this.items.push(loadEnvLocal())")

    assert "AppButton" in meta and "App Button" in meta  # the whole token and its parts
    assert "src" in meta and "components" in meta and "/" not in meta and "." not in meta
    assert "items" in body and "this.items" not in body  # `foo.bar` no longer a single token
    assert "load Env Local" in body


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("loadEnvLocal", "(loadenvlocal:* | (load & env & local))"),
        ("load env", "load:* | env:*"),
        ("snake_case_name", "snake & case & name"),
        ("how does authentication work", "authentication:* | work:*"),  # stopwords dropped
        ("AppButton", "(appbutton:* | (app & button))"),
        ("getHTTPResponse2", "(gethttpresponse2:* | (get & http & response2))"),
        ("API_KEY", "api & key"),
        ("db", "db"),
    ],
)
def test_build_tsquery(query: str, expected: str) -> None:
    assert build_tsquery(query) == expected


@pytest.mark.parametrize("query", ["", "   ", "a", "!!!", "&|!():*", "the of is", "..."])
def test_queries_without_searchable_terms_return_none(query: str) -> None:
    assert build_tsquery(query) is None


def test_hostile_input_becomes_harmless_lexemes() -> None:
    hostile = "'); DROP TABLE code_chunks; -- \\ \" ' <-> :* & | !"

    tsquery = build_tsquery(hostile)

    assert tsquery == "drop:* | table:* | code & chunks"
    assert set(tsquery) <= set("abcdefghijklmnopqrstuvwxyz0123456789:*&|() _".replace("_", ""))  # only lexemes + our operators


def test_query_terms_are_bounded_and_deduplicated() -> None:
    query = " ".join(f"term{i}" for i in range(40)) + " term1 term1"

    clauses = build_tsquery(query).split(" | ")

    assert len(clauses) == 12 and len(set(clauses)) == 12


@pytest.mark.parametrize(
    ("query", "key"),
    [
        ("runProcessingJob", "runprocessingjob"), ("match_enrolled_course", "matchenrolledcourse"),
        ("forgot password screen", "forgotpasswordscreen"), ("  AppButton ", "appbutton"), ("reset-password", "resetpassword"),
        ("push tokens", "pushtokens"),
    ],
)
def test_exact_key_for_name_like_queries(query: str, key: str) -> None:
    assert exact_key(query) == key


@pytest.mark.parametrize("query", ["how does a user reset their password", "five words is too many", "a b", "x", "", "foo.bar()", "a+b"])
def test_questions_and_punctuation_get_no_exact_key(query: str) -> None:
    assert exact_key(query) is None


def test_reciprocal_rank_fusion_rewards_agreement() -> None:
    fused = reciprocal_rank_fusion({"keyword": [10, 20, 30], "semantic": [30, 10, 40]}, k=60)

    assert [item for item, _, _ in fused] == [10, 30, 20, 40]  # found by both retrievers beats found by one
    scores = {item: score for item, score, _ in fused}
    assert scores[10] == pytest.approx(1 / 61 + 1 / 62) and scores[20] == pytest.approx(1 / 62)
    assert dict((i, r) for i, _, r in fused)[30] == {"keyword": 3, "semantic": 1}


def test_fusion_breaks_ties_by_best_rank_then_id() -> None:
    assert [i for i, _, _ in reciprocal_rank_fusion({"keyword": [5], "semantic": [3]}, k=60)] == [3, 5]
    assert reciprocal_rank_fusion({}, k=60) == []


def test_backfill_sql_in_the_migration_matches_the_code() -> None:
    migration = next(Path(__file__).resolve().parents[1].glob("database/migrations/versions/*_add_code_search_index.py"))

    assert SEARCH_VECTOR_BACKFILL_SQL in migration.read_text()


# --- the embedding provider (Google Gemini) ----------------------------------------------------------------

KEY = "test-gemini-key-not-real-123"


def _settings(**overrides: object) -> Settings:
    return Settings(gemini_api_key=KEY, embedding_dimensions=DIMS, _env_file=None, **overrides)  # type: ignore[arg-type]


def _raw(i: int, size: int = DIMS) -> list[float]:
    return [float(i) + 1.0] + [0.5] * (size - 1)  # deliberately not unit length


def _embedder(handler, **kwargs: object) -> GeminiEmbedder:  # type: ignore[no-untyped-def]
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    embedder = GeminiEmbedder(_settings(), client=client, sleep=fake_sleep, **kwargs)  # type: ignore[arg-type]
    embedder.sleeps = sleeps  # type: ignore[attr-defined]
    return embedder


def _ok(request: httpx.Request) -> httpx.Response:
    count = len(json.loads(request.content)["requests"])
    return httpx.Response(200, json={"embeddings": [{"values": _raw(i)} for i in range(count)]})


def _norm(vector: list[float]) -> float:
    return sum(x * x for x in vector) ** 0.5


def test_request_shape_authentication_and_unit_length_results() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok(request)

    embedder = _embedder(handler)

    vectors = asyncio.run(embedder.embed(["a", "b", "c"]))

    (request,) = seen
    body = json.loads(request.content)
    assert str(request.url) == "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:batchEmbedContents"
    assert request.headers["x-goog-api-key"] == KEY and "authorization" not in request.headers
    assert KEY not in str(request.url)  # the key is never part of a URL, which HTTP libraries log
    assert body == {
        "requests": [
            {
                "model": "models/gemini-embedding-001",
                "content": {"parts": [{"text": text}]},
                "taskType": "RETRIEVAL_DOCUMENT",
                "outputDimensionality": 1536,
            }
            for text in ("a", "b", "c")
        ]
    }
    assert [len(v) for v in vectors] == [1536, 1536, 1536]  # exactly the configured dimensionality
    assert all(_norm(v) == pytest.approx(1.0) for v in vectors)  # Gemini returns unit vectors only at 3072
    assert vectors[1][0] > vectors[0][0] * 0  # order preserved (Gemini has no per-item index)
    assert embedder.key == "gemini-embedding-001/1536"


def test_documents_and_queries_use_the_paired_task_types() -> None:
    tasks: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tasks.extend(item["taskType"] for item in json.loads(request.content)["requests"])
        return _ok(request)

    embedder = _embedder(handler)

    asyncio.run(embedder.embed(["chunk one", "chunk two"]))
    asyncio.run(embedder.embed_query("how is the quiz limit enforced"))

    assert tasks == ["RETRIEVAL_DOCUMENT", "RETRIEVAL_DOCUMENT", "CODE_RETRIEVAL_QUERY"]
    assert (DOCUMENT_TASK, QUERY_TASK) == ("RETRIEVAL_DOCUMENT", "CODE_RETRIEVAL_QUERY")  # Google's documented pair


def test_a_batch_is_limited_to_what_the_api_accepts() -> None:
    embedder = _embedder(_ok)

    assert len(asyncio.run(embedder.embed([f"t{i}" for i in range(MAX_BATCH_SIZE)]))) == MAX_BATCH_SIZE
    with pytest.raises(ValueError):
        asyncio.run(embedder.embed([f"t{i}" for i in range(MAX_BATCH_SIZE + 1)]))


def test_server_errors_are_retried_with_short_backoff_then_succeed() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(503, json={}) if len(attempts) < 3 else _ok(request)

    embedder = _embedder(handler)

    assert len(asyncio.run(embedder.embed(["x"]))) == 1
    assert len(attempts) == 3 and embedder.sleeps == [1.25, 2.25]  # type: ignore[attr-defined]


def _rate_limited(delay: str | None, *quotas: str, header: str | None = None) -> httpx.Response:
    details: list[dict[str, object]] = []
    if delay:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay})
    if quotas:
        details.append({"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": q} for q in quotas]})
    headers = {"retry-after": header} if header else {}
    return httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED", "message": f"echo {KEY}", "details": details}}, headers=headers)


def test_a_rate_limit_waits_for_the_delay_google_asks_for_and_then_succeeds() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return _rate_limited("7s", "EmbedContentRequestsPerMinutePerProjectPerModel-FreeTier") if len(calls) < 3 else _ok(request)

    embedder = _embedder(handler)

    assert len(asyncio.run(embedder.embed(["x"]))) == 1
    assert len(calls) == 3 and embedder.sleeps == [7.25, 7.25]  # type: ignore[attr-defined]  # the asked-for delay, not 1s/2s


def test_rate_limit_delay_can_come_from_the_retry_after_header_or_a_default_backoff() -> None:
    answers = iter([_rate_limited(None, header="12"), _rate_limited(None), _rate_limited(None)])

    def handler(request: httpx.Request) -> httpx.Response:
        return next(answers, None) or _ok(request)

    embedder = _embedder(handler)

    asyncio.run(embedder.embed(["x"]))

    assert embedder.sleeps == [12.25, 10.25, 20.25]  # type: ignore[attr-defined]  # the header, then 5s doubling per consecutive 429


def test_a_rate_limit_that_never_clears_is_retryable_and_reports_the_quota_without_secrets() -> None:
    embedder = _embedder(lambda request: _rate_limited("2s", "EmbedContentRequestsPerMinute-FreeTier", "EmbedContentTokensPerMinute-FreeTier"))

    with pytest.raises(EmbeddingProviderError) as caught:
        asyncio.run(embedder.embed(["x"]))

    error = caught.value
    assert error.retryable and error.status_code == 429 and error.retry_after == 2.0
    assert "EmbedContentRequestsPerMinute-FreeTier" in str(error) and "EmbedContentTokensPerMinute-FreeTier" in str(error)
    assert KEY not in str(error)  # the provider's message is never echoed
    assert len(embedder.sleeps) == 5  # type: ignore[attr-defined]  # six tries (embedding_rate_limit_attempts), five waits


def test_a_very_long_rate_limit_delay_is_left_to_the_job_retry_schedule() -> None:
    embedder = _embedder(lambda request: _rate_limited("1800s", "x"))  # "come back in 30 minutes"

    with pytest.raises(EmbeddingProviderError) as caught:
        asyncio.run(embedder.embed(["x"]))

    assert caught.value.retryable and embedder.sleeps == []  # type: ignore[attr-defined]  # no long sleep inside a job


@pytest.mark.parametrize("quota", ["EmbedContentRequestsPerDayPerProjectPerModel-FreeTier", "embed_content_daily_requests"])
def test_an_exhausted_daily_quota_is_not_retried(quota: str) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return _rate_limited("40s", quota)

    embedder = _embedder(handler)

    with pytest.raises(EmbeddingProviderError) as caught:
        asyncio.run(embedder.embed(["x"]))

    assert not caught.value.retryable and "daily quota exhausted" in str(caught.value) and len(calls) == 1


def test_a_search_never_waits_on_a_rate_limit() -> None:
    embedder = _embedder(lambda request: _rate_limited("7s", "EmbedContentRequestsPerMinute-FreeTier"))

    with pytest.raises(EmbeddingProviderError):
        asyncio.run(embedder.embed_query("find me"))

    assert embedder.sleeps == []  # type: ignore[attr-defined]  # the search falls back to keyword results at once


@pytest.mark.parametrize("status", [500, 502, 503])
def test_persistent_transient_failure_is_reported_as_retryable(status: int) -> None:
    embedder = _embedder(lambda request: httpx.Response(status, json={}))

    with pytest.raises(EmbeddingProviderError) as caught:
        asyncio.run(embedder.embed(["x"]))

    assert caught.value.retryable and caught.value.status_code == status


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (400, {"error": {"status": "INVALID_ARGUMENT", "details": [{"reason": "API_KEY_INVALID"}]}}, "rejected the API key"),
        (401, {"error": {}}, "rejected the API key"),
        (403, {"error": {"status": "PERMISSION_DENIED"}}, "rejected the API key"),
        (400, {"error": {"message": "invalid argument"}}, "returned 400"),
        (404, {"error": {"message": "model not found"}}, "returned 404"),
    ],
)
def test_client_errors_are_permanent_and_not_retried(status: int, body: dict, message: str) -> None:  # type: ignore[type-arg]
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(status, json={**body, "echo": KEY})

    embedder = _embedder(handler)

    with pytest.raises(EmbeddingProviderError) as caught:
        asyncio.run(embedder.embed(["x"]))

    assert not caught.value.retryable and len(calls) == 1 and message in str(caught.value)
    assert KEY not in str(caught.value)  # provider bodies are never echoed


def test_network_errors_are_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(EmbeddingProviderError) as caught:
        asyncio.run(_embedder(handler).embed(["x"], attempts=2))

    assert caught.value.retryable


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"unexpected": True}),
        httpx.Response(200, json={"embeddings": [{"values": _raw(0, 3072)}]}),  # the model's default width: dimensionality ignored
        httpx.Response(200, json={"embeddings": [{"values": _raw(0, 768)}]}),  # wrong width
        httpx.Response(200, json={"embeddings": []}),  # wrong count
        httpx.Response(200, json={"embeddings": [{"values": [0.0] * DIMS}]}),  # unusable (zero) vector
        httpx.Response(200, json={"embeddings": [{"values": ["a"] * DIMS}]}),  # not numbers
    ],
)
def test_malformed_or_wrongly_sized_responses_are_permanent_errors(response: httpx.Response) -> None:
    with pytest.raises(EmbeddingProviderError) as caught:
        asyncio.run(_embedder(lambda request: response).embed(["x"]))

    assert not caught.value.retryable  # nothing wrong-sized can ever be stored


def test_query_embeddings_are_cached_and_use_a_single_attempt() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, json={}) if len(calls) == 1 else _ok(request)

    embedder = _embedder(handler)
    with pytest.raises(EmbeddingProviderError):
        asyncio.run(embedder.embed_query("find me"))  # one attempt only: a search must stay fast
    assert len(calls) == 1

    first = asyncio.run(embedder.embed_query("find me"))
    second = asyncio.run(embedder.embed_query("find me"))

    assert first == second and len(calls) == 2  # the repeat was served from the cache


def test_the_key_comes_from_gemini_api_key_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-used")

    assert Settings(_env_file=None).embeddings_enabled is False  # an OpenAI key does nothing
    with pytest.raises(EmbeddingNotConfiguredError, match="GEMINI_API_KEY"):
        GeminiEmbedder(Settings(_env_file=None))

    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    assert Settings(_env_file=None).embeddings_enabled is True
    assert GeminiEmbedder(Settings(_env_file=None)).key == "gemini-embedding-001/1536"


def test_defaults_target_gemini_embedding_001_at_1536_dimensions() -> None:
    defaults = Settings(_env_file=None)

    assert (defaults.embedding_model, defaults.embedding_dimensions) == ("gemini-embedding-001", 1536)
    assert defaults.embedding_api_base_url == "https://generativelanguage.googleapis.com/v1beta"
    assert defaults.embedding_dimensions == EMBEDDING_DIMENSIONS  # the database column width


def test_configuration_errors_and_secret_handling() -> None:
    with pytest.raises(EmbeddingNotConfiguredError):
        GeminiEmbedder(Settings(gemini_api_key=None, _env_file=None))
    with pytest.raises(EmbeddingNotConfiguredError, match="migration"):
        GeminiEmbedder(Settings(gemini_api_key="x", embedding_dimensions=768, _env_file=None))

    settings = _settings()
    assert settings.embeddings_enabled
    assert KEY not in repr(settings) and KEY not in str(settings.model_dump())
    assert model_key(settings) == "gemini-embedding-001/1536"
    assert model_key(_settings(embedding_model="other-model")) == "other-model/1536"  # models never share a cache key
