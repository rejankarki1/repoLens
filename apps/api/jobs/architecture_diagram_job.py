"""RQ job that generates a repository's AI architecture diagram. Runs on the existing ingestion queue and
worker, same as `architecture_job.py`'s deterministic analysis — but unlike that one, this calls a paid AI
provider, so a transient failure (timeout, rate limit, 5xx) re-raises out of `run_diagram` to let RQ's
`Retry` policy (`enqueue_architecture_diagram`) re-run it after a backoff; a non-transient failure (a bad
request, a malformed model response, the deterministic analysis being stale) is recorded on the row and
returns normally, so RQ never pointlessly retries something that would fail the same way again.
"""

import asyncio
import logging

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.config import get_settings
from apps.api.services.architecture_diagram.store import run_diagram

logger = logging.getLogger(__name__)


def generate_architecture_diagram_job(repository_id: int) -> None:
    """RQ entry point. Must be importable by path in the worker process."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(_run(repository_id))


async def _run(repository_id: int) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=pool.NullPool)
    try:
        await run_diagram(async_sessionmaker(engine, expire_on_commit=False), repository_id, settings=settings)
    finally:
        await engine.dispose()
