"""RQ job that builds a repository's embedding index. Runs on the existing ingestion queue and worker.

Independent of source ingestion: ingestion is already `completed` (files, chunks and the keyword index
exist) before this job is enqueued, so a missing key or a provider outage only leaves semantic search
unavailable. The job is idempotent and resumable (see `services/embeddings.py`), which makes recovery
simple: a transient provider failure raises so RQ retries with backoff, a permanent one is logged and
dropped, and any later job or the manual `POST /repositories/{id}/embeddings` picks up the missing chunks.
"""

import asyncio
import logging

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from apps.api.config import Settings, get_settings
from apps.api.services.embeddings import EmbeddingError, GeminiEmbedder, build_embeddings

logger = logging.getLogger(__name__)


class TransientEmbeddingError(Exception):
    """A retryable provider failure. Raising it applies the job's `Retry` policy."""


def embed_repository_job(repository_id: int) -> None:
    """RQ entry point. Must be importable by path in the worker process."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(_run(repository_id))


async def _run(repository_id: int) -> None:
    settings = get_settings()
    if not settings.embeddings_enabled:
        logger.info("Skipping embeddings for repository %s: GEMINI_API_KEY is not set", repository_id)
        return
    engine = create_async_engine(settings.database_url, poolclass=pool.NullPool)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        await run_embedding(engine, sessions, repository_id, GeminiEmbedder(settings), settings)
    finally:
        await engine.dispose()


async def run_embedding(
    engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
    repository_id: int,
    embedder: GeminiEmbedder,
    settings: Settings | None = None,
) -> None:
    try:
        stats = await build_embeddings(engine, sessions, repository_id, embedder, settings)
    except EmbeddingError as exc:
        if exc.retryable:
            raise TransientEmbeddingError(str(exc)) from exc
        logger.error("Embedding repository %s failed permanently: %s", repository_id, exc)
        return
    if stats.already_running:
        logger.info("Embedding repository %s skipped: another embedding job is running", repository_id)
        return
    logger.info(
        "Embedded repository %s: %s new, %s reused, %s deferred, %s batches, %.0f ms",
        repository_id, stats.embedded, stats.already_embedded, stats.skipped, stats.batches, stats.duration_ms,
    )
