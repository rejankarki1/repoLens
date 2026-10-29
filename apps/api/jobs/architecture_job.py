"""RQ job that builds a repository's architecture graph. Runs on the existing ingestion queue and worker.

Everything it needs is already in PostgreSQL (the ingested source files), and the analysis is deterministic,
so there is no network call and no retry: the outcome, including a failure, is recorded on the repository's
`architecture_analyses` row (see `services/architecture/store.py`).
"""

import asyncio
import logging

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.config import get_settings
from apps.api.services.architecture.store import run_analysis

logger = logging.getLogger(__name__)


def analyze_architecture_job(repository_id: int) -> None:
    """RQ entry point. Must be importable by path in the worker process."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(_run(repository_id))


async def _run(repository_id: int) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=pool.NullPool)
    try:
        await run_analysis(async_sessionmaker(engine, expire_on_commit=False), repository_id, settings)
    finally:
        await engine.dispose()
