"""Housekeeping for background ingestion: stale-job recovery and orphaned temporary archives.

Both run on a best-effort basis. PostgreSQL stays the source of truth; RQ is only consulted to avoid
recovering a job that is genuinely still alive.
"""

import asyncio
import logging
import tempfile
import time
from pathlib import Path

from rq import Queue
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.config import get_settings
from apps.api.core.queue import get_ingestion_queue, live_repository_ids
from apps.api.services.ingestion import recover_stale_ingestions

logger = logging.getLogger(__name__)

# Must match the prefix used by `GitHubArchiveService._stream_to_tempfile`.
ARCHIVE_PREFIX = "repolens-archive-"


async def sweep_stale_ingestions(db: AsyncSession, queue: Queue, repository_id: int | None = None) -> list[int]:
    """Recover stale ingestions, sparing any repository RQ still has a live job for.

    If Redis cannot be queried nothing is recovered: guessing could fail a job that is running.
    """
    try:
        live = await asyncio.to_thread(live_repository_ids, queue)
    except Exception:
        logger.warning("Skipping stale-ingestion recovery: could not read RQ state", exc_info=True)
        return []
    return await recover_stale_ingestions(db, live, repository_id)


async def sweeper_loop(
    sessions: async_sessionmaker[AsyncSession], queue: Queue, interval: float
) -> None:
    """Periodically recover stale ingestions (runs inside the API process)."""
    while True:
        await asyncio.sleep(interval)
        try:
            async with sessions() as db:
                await sweep_stale_ingestions(db, queue)
        except Exception:
            logger.exception("Stale-ingestion sweep failed")


def cleanup_orphan_archives(max_age_seconds: float, directory: Path | None = None) -> list[Path]:
    """Delete leftover `repolens-archive-*.zip` temp files older than `max_age_seconds`.

    Safe next to other workers: a live job is killed at the job timeout, so a temp file older than
    the (larger) configured maximum age cannot belong to one. Only plain files with our prefix are
    touched, and anything that cannot be deleted is skipped.
    """
    root = directory or Path(tempfile.gettempdir())
    cutoff = time.time() - max_age_seconds
    removed: list[Path] = []
    for path in root.glob(f"{ARCHIVE_PREFIX}*.zip"):
        try:
            if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed.append(path)
        except OSError:
            logger.warning("Could not remove orphaned archive %s", path, exc_info=True)
    return removed


async def _startup_recovery() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=pool.NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            await sweep_stale_ingestions(db, get_ingestion_queue())
    finally:
        await engine.dispose()


def run_startup_maintenance() -> None:
    """Called once when a worker starts: recover stale rows and remove orphaned temp archives."""
    try:
        asyncio.run(_startup_recovery())
    except Exception:
        logger.exception("Startup stale-ingestion recovery failed")
    removed = cleanup_orphan_archives(get_settings().ingestion_orphan_archive_max_age_seconds)
    if removed:
        logger.info("Removed %s orphaned temporary archives", len(removed))
