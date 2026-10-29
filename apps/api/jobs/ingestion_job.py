"""RQ job that runs one repository ingestion in a worker process.

RQ is synchronous, so the job is a thin sync entry point that runs the existing async pipeline
(`ingest_repository`: claim queued -> download -> read -> persist) on its own event loop. PostgreSQL stays
the source of truth: the pipeline records `in_progress` / `completed` / `failed` itself, so the
outcome never depends on RQ's own job state.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from rq import get_current_job
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.config import get_settings
from apps.api.services.github_archive import GitHubArchiveService
from apps.api.services.ingestion import (
    IngestionFailedError,
    IngestionNotQueuedError,
    ingest_repository,
    touch_heartbeat,
)
from apps.api.services.source_file_persistence import UnknownRepositoryError

logger = logging.getLogger(__name__)


class TransientIngestionError(Exception):
    """A retryable failure. Raising it is what makes RQ apply the job's `Retry` policy."""


def ingest_repository_job(repository_id: int) -> None:
    """RQ entry point: ingest one repository. Must be importable by path in the worker process."""
    # A spawned work-horse is a fresh interpreter with no logging configured, which would drop the
    # INFO indexing metrics. No-op when the process is already configured (e.g. a forked Worker).
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    job = get_current_job()
    # `retries_left` is the number of retries still available *after* this attempt fails.
    completed = asyncio.run(_run(repository_id, will_retry=bool(job and job.retries_left)))
    if completed is True:
        _queue_architecture(repository_id)  # deterministic and free of any provider key, so always
        if get_settings().embeddings_enabled:
            _enqueue_embedding(repository_id)


def _enqueue_embedding(repository_id: int) -> None:
    """Start building the semantic index. Ingestion is already `completed`, so a failure here (Redis
    hiccup, anything) is only logged: `POST /repositories/{id}/embeddings` can start it later."""
    try:
        from apps.api.core.queue import enqueue_embedding, get_ingestion_queue

        enqueue_embedding(get_ingestion_queue(), repository_id)
    except Exception:
        logger.warning("Could not enqueue embeddings for repository %s", repository_id, exc_info=True)


def _queue_architecture(repository_id: int) -> None:
    """Start the architecture analysis of the freshly ingested commit. Ingestion is already `completed`, so a failure
    here is only logged: `POST /repositories/{id}/architecture` can start it later."""
    try:
        asyncio.run(_queue_architecture_async(repository_id))
    except Exception:
        logger.warning("Could not queue architecture analysis for repository %s", repository_id, exc_info=True)


async def _queue_architecture_async(repository_id: int) -> None:
    from apps.api.core.queue import enqueue_architecture, get_ingestion_queue
    from apps.api.services.architecture.store import queue_analysis

    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=pool.NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            queue = get_ingestion_queue()
            await queue_analysis(db, repository_id, lambda: enqueue_architecture(queue, repository_id, settings), settings=settings)
    finally:
        await engine.dispose()


async def _run(repository_id: int, will_retry: bool = False) -> bool:
    # A fresh engine per job: asyncpg connections are bound to the event loop `asyncio.run` created.
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=pool.NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    # The heartbeat uses its own short sessions (separate connections), so it keeps ticking while the
    # main session is busy downloading or holding the persistence transaction.
    heartbeat = asyncio.create_task(
        heartbeat_loop(sessions, repository_id, settings.ingestion_heartbeat_interval_seconds)
    )
    try:
        async with sessions() as db:
            async with GitHubArchiveService() as archives:
                return await run_ingestion(db, repository_id, archives, will_retry=will_retry)
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        await engine.dispose()


async def heartbeat_loop(
    sessions: Callable[[], AbstractAsyncContextManager[AsyncSession]], repository_id: int, interval: float
) -> None:
    """Refresh `ingestion_heartbeat_at` every `interval` seconds until cancelled.

    A failed tick (database hiccup, lock timeout during the file swap) is logged and skipped; the
    next tick tries again. Stale detection tolerates several missed ticks.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            async with sessions() as db:
                await touch_heartbeat(db, repository_id)
        except Exception:
            logger.warning("Heartbeat update failed for repository %s", repository_id, exc_info=True)


async def run_ingestion(
    db: AsyncSession, repository_id: int, archives: GitHubArchiveService, *, will_retry: bool = False
) -> bool:
    """Run the pipeline and translate its outcome into "return" (done) or "raise" (retry).

    Returns True only when the repository was ingested and is now `completed`.

    Returning means the job is finished and PostgreSQL holds the result, including permanent
    failures, which are already recorded as `failed` with a safe message. Only transient failures
    raise, so RQ retries them with backoff.
    """
    try:
        files_stored = await ingest_repository(db, repository_id, archives, will_retry=will_retry)
    except UnknownRepositoryError:
        logger.warning("Ingestion job for repository %s skipped: repository no longer exists", repository_id)
    except IngestionNotQueuedError:
        logger.warning("Ingestion job for repository %s skipped: repository is not queued", repository_id)
    except IngestionFailedError as exc:
        if exc.retryable:
            raise TransientIngestionError(exc.message) from exc
        logger.warning("Ingestion of repository %s failed permanently: %s", repository_id, exc.message)
    else:
        logger.info("Ingested repository %s: %s files stored", repository_id, files_stored)
        return True
    return False
