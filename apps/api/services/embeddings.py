"""Embeddings for semantic code search: one provider (Google Gemini), batched, bounded, resumable.

Provider: Google `gemini-embedding-001`, requested at exactly 1,536 dimensions (`outputDimensionality`),
through the native `batchEmbedContents` endpoint. The OpenAI-compatible endpoint is not used because it
cannot set a task type, and Gemini embeds documents and queries with *paired* task types:

* chunks are embedded as `RETRIEVAL_DOCUMENT`;
* search queries are embedded as `CODE_RETRIEVAL_QUERY` (Google's task for finding code from a
  natural-language query, designed to be compared with `RETRIEVAL_DOCUMENT` vectors).

Vectors below the model's native 3,072 dimensions are not unit length, so they are L2-normalized here.
Configured only through the backend environment (`GEMINI_API_KEY`, see `Settings`); the key travels in the
`x-goog-api-key` header (never in the URL, which libraries log). Without a key semantic search is disabled
and keyword search keeps working. Details and pricing notes: docs/search.md.

Vectors live in `code_embeddings`, keyed by (content hash, model), so unchanged chunks are never
embedded twice and embeddings from different models or commits never mix. `build_embeddings` embeds only
the chunks of a repository's current snapshot that lack a vector, commits after every batch (so a
failure keeps all finished work and a retry resumes), and takes a PostgreSQL advisory lock so two jobs
never embed the same repository at once.

If the document/query task types or the dimensionality are ever changed, change the model key too
(`model_key`), otherwise old and new vectors would be mixed.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import lru_cache

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from apps.api.config import Settings, get_settings
from apps.api.models.entities import EMBEDDING_DIMENSIONS, CodeChunk, CodeEmbedding, Repository, SourceFile

logger = logging.getLogger(__name__)

EMBEDDING_LOCK_NAMESPACE = 0x52_45_4D_42  # first key of the two-int advisory lock; the second is the repository id
MAX_INPUT_CHARS = 6_000  # a chunk is at most ~1.5 KB; this guards the model's 2,048-token input limit
MAX_BATCH_SIZE = 100  # Gemini's batchEmbedContents accepts at most 100 requests per call
DOCUMENT_TASK = "RETRIEVAL_DOCUMENT"  # how chunks are embedded
QUERY_TASK = "CODE_RETRIEVAL_QUERY"  # how search queries are embedded: the matching query-side task for code


class EmbeddingError(Exception):
    """Base class. `retryable` tells the caller whether trying again later can help."""

    retryable = False


class EmbeddingNotConfiguredError(EmbeddingError):
    """No API key is configured."""


class EmbeddingProviderError(EmbeddingError):
    def __init__(
        self, message: str, *, retryable: bool, status_code: int | None = None, retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after = retry_after  # seconds the provider asked us to wait, when it said


def model_key(settings: Settings) -> str:
    """The identity stored with every vector: model and width, so different setups never mix."""
    return f"{settings.embedding_model}/{settings.embedding_dimensions}"


class GeminiEmbedder:
    """Calls `POST {base_url}/models/{model}:batchEmbedContents`. Errors are classified as retryable
    (429, 5xx, network) or permanent."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        settings = settings or get_settings()
        if not settings.embeddings_enabled:
            raise EmbeddingNotConfiguredError("GEMINI_API_KEY is not set")
        if settings.embedding_dimensions != EMBEDDING_DIMENSIONS:
            raise EmbeddingNotConfiguredError(
                f"embedding_dimensions={settings.embedding_dimensions} does not match the database column "
                f"({EMBEDDING_DIMENSIONS}); changing it needs a migration"
            )
        assert settings.gemini_api_key is not None
        self.model = settings.embedding_model
        self.dimensions = settings.embedding_dimensions
        self.key = model_key(settings)
        self._url = f"{settings.embedding_api_base_url.rstrip('/')}/models/{self.model}:batchEmbedContents"
        self._headers = {"x-goog-api-key": settings.gemini_api_key.get_secret_value()}
        self._timeout = settings.embedding_request_timeout_seconds
        self._query_timeout = settings.embedding_query_timeout_seconds
        self._rate_limit_attempts = settings.embedding_rate_limit_attempts
        self._max_rate_limit_wait = settings.embedding_max_rate_limit_wait_seconds
        self._client = client
        self._sleep = sleep
        self._query_cache: OrderedDict[str, list[float]] = OrderedDict()

    async def embed(
        self,
        texts: list[str],
        *,
        task: str = DOCUMENT_TASK,
        attempts: int = 3,
        rate_limit_attempts: int | None = None,
        timeout: float | None = None,
    ) -> list[list[float]]:
        """Embed `texts` in one request (at most `MAX_BATCH_SIZE`).

        Network errors and 5xx are retried `attempts` times with short backoff. A rate limit (429) is retried
        up to `rate_limit_attempts` times, waiting the delay the provider asks for (capped at
        `embedding_max_rate_limit_wait_seconds`; a longer ask is raised immediately for the RQ retry schedule).
        """
        if not texts:
            return []
        if len(texts) > MAX_BATCH_SIZE:
            raise ValueError(f"at most {MAX_BATCH_SIZE} texts per request")
        body = {
            "requests": [
                {
                    "model": f"models/{self.model}",
                    "content": {"parts": [{"text": t[:MAX_INPUT_CHARS]}]},
                    "taskType": task,
                    "outputDimensionality": self.dimensions,
                }
                for t in texts
            ]
        }
        limit_tries = self._rate_limit_attempts if rate_limit_attempts is None else rate_limit_attempts
        errors = rate_limits = 0
        while True:
            try:
                return await self._request(body, len(texts), timeout or self._timeout)
            except EmbeddingProviderError as exc:
                if not exc.retryable:
                    raise
                if exc.status_code == 429:
                    rate_limits += 1
                    if rate_limits >= limit_tries:
                        raise
                    wait = exc.retry_after if exc.retry_after is not None else min(5.0 * 2 ** (rate_limits - 1), 60.0)
                    if wait > self._max_rate_limit_wait:
                        raise  # e.g. "retry in 40 minutes": do not hold the job; RQ retries later
                    logger.info("Embedding rate limited; waiting %.0fs (%s/%s)", wait, rate_limits, limit_tries)
                else:
                    errors += 1
                    if errors >= attempts:
                        raise
                    wait = min(2.0 ** (errors - 1), 20.0)
                await self._sleep(wait + 0.25)

    async def embed_query(self, query: str) -> list[float]:
        """One embedding for a search query: a single short attempt, cached for repeated searches."""
        cached = self._query_cache.get(query)
        if cached is not None:
            self._query_cache.move_to_end(query)
            return cached
        # One attempt, no waiting: a search that cannot get a vector right now falls back to keyword results.
        (vector,) = await self.embed([query], task=QUERY_TASK, attempts=1, rate_limit_attempts=1, timeout=self._query_timeout)
        self._query_cache[query] = vector
        if len(self._query_cache) > 256:
            self._query_cache.popitem(last=False)
        return vector

    async def _request(self, body: dict[str, object], count: int, timeout: float) -> list[list[float]]:
        try:
            if self._client is not None:
                response = await self._client.post(self._url, json=body, headers=self._headers, timeout=timeout)
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(self._url, json=body, headers=self._headers, timeout=timeout)
        except httpx.HTTPError as exc:  # timeouts, connection errors
            raise EmbeddingProviderError(f"embedding request failed: {type(exc).__name__}", retryable=True) from exc
        status = response.status_code
        if status == 429:
            raise _rate_limit_error(response)
        if status >= 500:
            raise EmbeddingProviderError(f"embedding provider returned {status}", retryable=True, status_code=status)
        # Google reports an invalid key as HTTP 400 (reason API_KEY_INVALID), not 401.
        if status in (401, 403) or (status == 400 and "API_KEY_INVALID" in response.text):
            raise EmbeddingProviderError("embedding provider rejected the API key", retryable=False, status_code=status)
        if status >= 400:
            raise EmbeddingProviderError(f"embedding provider returned {status}", retryable=False, status_code=status)
        try:
            vectors = [[float(x) for x in item["values"]] for item in response.json()["embeddings"]]
        except (ValueError, KeyError, TypeError) as exc:
            raise EmbeddingProviderError("embedding provider sent an unexpected response", retryable=False) from exc
        if len(vectors) != count or any(len(v) != self.dimensions for v in vectors):
            raise EmbeddingProviderError(
                f"embedding provider returned {len(vectors)} vectors that are not all {self.dimensions} dimensions",
                retryable=False,
            )
        return [_unit(v) for v in vectors]


def _rate_limit_error(response: httpx.Response) -> EmbeddingProviderError:
    """A 429 with what Google tells us: which quota was hit and how long to wait (never the request or key).

    Google's error body carries `RetryInfo.retryDelay` ("34s") and `QuotaFailure.violations[].quotaId`
    (e.g. a per-minute vs a per-day limit). A per-day quota will not clear within a retry schedule, so it is
    reported as permanent for this run instead of being retried pointlessly.
    """
    delay: float | None = None
    quotas: list[str] = []
    try:
        header = response.headers.get("retry-after")
        delay = float(header) if header else None
    except ValueError:
        delay = None
    try:
        for detail in response.json().get("error", {}).get("details", []):
            kind = str(detail.get("@type", ""))
            if kind.endswith("RetryInfo"):
                delay = float(str(detail.get("retryDelay", "")).rstrip("s"))
            elif kind.endswith("QuotaFailure"):
                quotas += [str(v.get("quotaId") or v.get("quotaMetric")) for v in detail.get("violations", [])]
    except (ValueError, TypeError, AttributeError):
        pass
    summary = f" ({', '.join(dict.fromkeys(quotas))})" if quotas else ""
    if any("perday" in q.lower().replace("_", "").replace("-", "") or "daily" in q.lower() for q in quotas):
        logger.warning("Gemini daily quota exhausted%s; it resets at midnight Pacific time", summary)
        return EmbeddingProviderError(
            f"embedding provider daily quota exhausted{summary}", retryable=False, status_code=429, retry_after=delay
        )
    logger.warning("Gemini rate limit%s; retry delay %s", summary, f"{delay:.0f}s" if delay is not None else "not given")
    return EmbeddingProviderError(
        f"embedding provider rate limit{summary}", retryable=True, status_code=429, retry_after=delay
    )


def _unit(vector: list[float]) -> list[float]:
    """L2-normalize: Gemini only returns unit-length vectors at its native 3,072 dimensions."""
    norm = math.sqrt(sum(x * x for x in vector))
    if not math.isfinite(norm) or norm == 0.0:
        raise EmbeddingProviderError("embedding provider returned an unusable vector", retryable=False)
    return [x / norm for x in vector]


@lru_cache
def _shared_embedder() -> GeminiEmbedder | None:
    try:
        return GeminiEmbedder()
    except EmbeddingNotConfiguredError:
        return None


def get_embedder() -> GeminiEmbedder | None:
    """FastAPI dependency: the process-wide embedder, or None when semantic search is not configured."""
    return _shared_embedder()


@dataclass(frozen=True, slots=True)
class EmbedStats:
    embedded: int  # chunk texts embedded in this run
    already_embedded: int  # distinct current chunk texts that had a vector before this run
    skipped: int  # texts left for a later run because of the per-run bound
    batches: int
    duration_ms: float
    already_running: bool = False


@asynccontextmanager
async def embedding_lock(engine: AsyncEngine, repository_id: int) -> AsyncIterator[bool]:
    """Session-level advisory lock for one repository, held on its own connection for the whole job.

    PostgreSQL drops it when the connection closes, so a crashed worker can never leave it stuck.
    """
    async with engine.connect() as connection:
        got = (
            await connection.execute(
                text("SELECT pg_try_advisory_lock(:ns, :repo)"), {"ns": EMBEDDING_LOCK_NAMESPACE, "repo": repository_id}
            )
        ).scalar_one()
        try:
            yield bool(got)
        finally:
            if got:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:ns, :repo)"), {"ns": EMBEDDING_LOCK_NAMESPACE, "repo": repository_id}
                )


async def embedding_job_running(db: AsyncSession, repository_id: int) -> bool:
    """Whether an embedding job currently holds the repository's lock (visible to every connection)."""
    row = await db.execute(
        text("SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND classid = :ns AND objid = :repo AND granted LIMIT 1"),
        {"ns": EMBEDDING_LOCK_NAMESPACE, "repo": repository_id},
    )
    return row.first() is not None


async def build_embeddings(
    engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
    repository_id: int,
    embedder: GeminiEmbedder,
    settings: Settings | None = None,
) -> EmbedStats:
    """Embed the current snapshot's chunks that have no vector yet (bounded, resumable, idempotent)."""
    settings = settings or get_settings()
    started = time.perf_counter()
    async with embedding_lock(engine, repository_id) as acquired:
        if not acquired:
            return EmbedStats(0, 0, 0, 0, 0.0, already_running=True)
        async with sessions() as db:
            commit = (
                await db.execute(select(Repository.last_ingested_commit_sha).where(Repository.id == repository_id))
            ).scalar_one_or_none()
            if commit is None:
                return EmbedStats(0, 0, 0, 0, (time.perf_counter() - started) * 1000)
            scope = (SourceFile.repository_id == repository_id, SourceFile.commit_sha == commit)
            current = select(CodeChunk.content_hash).join(SourceFile).where(*scope).distinct()
            total = (await db.execute(select(func.count()).select_from(current.subquery()))).scalar_one()
            missing = (
                await db.execute(
                    select(CodeChunk.content_hash, func.min(CodeChunk.content))
                    .join(SourceFile)
                    .where(
                        *scope,
                        ~select(1)
                        .where(CodeEmbedding.content_hash == CodeChunk.content_hash, CodeEmbedding.model == embedder.key)
                        .exists(),
                    )
                    .group_by(CodeChunk.content_hash)
                    .order_by(CodeChunk.content_hash)
                    .limit(settings.embedding_max_chunks_per_repository + 1)
                )
            ).all()
            skipped = max(0, len(missing) - settings.embedding_max_chunks_per_repository)
            missing = missing[: settings.embedding_max_chunks_per_repository]

            embedded = batches = 0
            batch_size = max(1, min(settings.embedding_batch_size, MAX_BATCH_SIZE))
            for start in range(0, len(missing), batch_size):
                batch = missing[start : start + batch_size]
                vectors = await embedder.embed([content for _, content in batch], task=DOCUMENT_TASK)
                await db.execute(
                    pg_insert(CodeEmbedding)
                    .values([{"content_hash": h, "model": embedder.key, "embedding": v} for (h, _), v in zip(batch, vectors)])
                    .on_conflict_do_nothing()
                )
                await db.commit()  # finished batches survive a later failure; a retry resumes from here
                embedded += len(batch)
                batches += 1
    if skipped:
        logger.warning("Embedding limit reached for repository %s: %s chunk texts left for a later run", repository_id, skipped)
    return EmbedStats(
        embedded=embedded,
        already_embedded=max(0, total - len(missing) - skipped),
        skipped=skipped,
        batches=batches,
        duration_ms=(time.perf_counter() - started) * 1000,
    )
