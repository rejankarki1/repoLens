"""Repository ingestion endpoints."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from rq import Queue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.database import get_db
from apps.api.core.queue import enqueue_ingestion, get_queue
from apps.api.jobs.maintenance import sweep_stale_ingestions
from apps.api.models.entities import Repository
from apps.api.schemas.ingestion import IngestionStatusResponse
from apps.api.services.ingestion import IngestionInProgressError, abort_enqueue, request_ingestion
from apps.api.services.source_file_persistence import UnknownRepositoryError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ingestion"])


def _status_response(repository: Repository) -> IngestionStatusResponse:
    return IngestionStatusResponse(
        repository_id=repository.id,
        status=repository.ingestion_status,
        commit_sha=repository.last_ingested_commit_sha,
        truncated=repository.ingestion_truncated,
        last_ingested_at=repository.last_ingested_at,
        error=repository.ingestion_error,
    )


async def _get_repository_or_404(db: AsyncSession, repository_id: int) -> Repository:
    result = await db.execute(
        select(Repository).where(Repository.id == repository_id).execution_options(populate_existing=True)
    )
    repository = result.scalar_one_or_none()
    if repository is None:
        raise HTTPException(status_code=404, detail=f"Repository {repository_id} not found")
    return repository


@router.post(
    "/repositories/{repository_id}/ingestions",
    response_model=IngestionStatusResponse,
    status_code=202,
)
async def create_ingestion(
    repository_id: int,
    db: AsyncSession = Depends(get_db),
    queue: Queue = Depends(get_queue),
) -> IngestionStatusResponse:
    """Queue a background ingestion. Returns 202 once the job is in Redis; poll `ingestion-status`."""
    try:
        try:
            await request_ingestion(db, repository_id)
        except IngestionInProgressError:
            # It may be stuck after a crash. Recover it only if it is stale and RQ has no live job
            # for it, then try once more; otherwise the 409 below is genuine.
            if repository_id not in await sweep_stale_ingestions(db, queue, repository_id):
                raise
            await request_ingestion(db, repository_id)
    except UnknownRepositoryError as exc:
        raise HTTPException(status_code=404, detail=f"Repository {repository_id} not found") from exc
    except IngestionInProgressError as exc:
        raise HTTPException(status_code=409, detail="An ingestion for this repository is already in progress") from exc

    try:
        # RQ's Redis client is synchronous; keep it off the event loop.
        await asyncio.to_thread(enqueue_ingestion, queue, repository_id)
    except Exception as exc:
        logger.exception("Could not enqueue ingestion for repository %s", repository_id)
        await abort_enqueue(db, repository_id, "Could not queue the ingestion; please try again")
        raise HTTPException(status_code=503, detail="Could not queue the ingestion; please try again") from exc

    return _status_response(await _get_repository_or_404(db, repository_id))


@router.get(
    "/repositories/{repository_id}/ingestion-status",
    response_model=IngestionStatusResponse,
    status_code=200,
)
async def get_ingestion_status(repository_id: int, db: AsyncSession = Depends(get_db)) -> IngestionStatusResponse:
    """Current ingestion state and the commit of the stored snapshot."""
    return _status_response(await _get_repository_or_404(db, repository_id))
