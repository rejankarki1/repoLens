"""Repository-scoped Architecture Diagram V2 endpoints: the AI-identified functional-component graph on
top of the deterministic one (`routes/architecture.py`). Same shape, three endpoints instead of four (no
per-node detail endpoint — the whole diagram is small enough to return in one piece, unlike the file-level
dependency graph)."""

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from rq import Queue
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import Settings
from apps.api.core.auth import settings_dep
from apps.api.core.database import get_db
from apps.api.core.limits import get_limiter, guest_read_limit
from apps.api.core.queue import enqueue_architecture_diagram, get_queue
from apps.api.schemas.architecture_diagram import DiagramQueueResponse, DiagramResponse, DiagramStatus
from apps.api.services.access import RepoAccess, member_write, read_access
from apps.api.services.architecture_diagram import store
from apps.api.services.quota import refund_hourly, reserve_hourly
from apps.api.services.rate_limit import Limiter

router = APIRouter(tags=["architecture-diagram"])


@router.post("/repositories/{repository_id}/architecture-diagram", response_model=DiagramQueueResponse, status_code=202)
async def request_architecture_diagram(
    repository_id: int,
    response: Response,
    force: bool = Query(False, description="Regenerate even when the stored result is current"),
    db: AsyncSession = Depends(get_db),
    queue: Queue = Depends(get_queue),
    access: RepoAccess = Depends(member_write),
    settings: Settings = Depends(settings_dep),
    limiter: Limiter = Depends(get_limiter),
) -> DiagramQueueResponse:
    """Queue an AI diagram generation for the repository's current ingested commit (safe to repeat).

    202 when a job was queued or one is already running; 200 when the stored result is already current.
    409 if the deterministic analysis (`POST /architecture`) is not itself current yet — request that first.
    """
    await reserve_hourly(
        limiter, "architecture-diagram", access.user_id, settings.architecture_diagram_hourly_limit,
        f"You have requested {settings.architecture_diagram_hourly_limit} diagrams in the last hour.",
    )
    enqueued = False
    try:
        status, enqueued = await store.queue_diagram(
            db, repository_id, lambda: enqueue_architecture_diagram(queue, repository_id), force=force
        )
    except store.RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except store.DeterministicAnalysisNotReadyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except store.EnqueueError as exc:
        raise HTTPException(status_code=503, detail="Could not queue the diagram generation job; please try again") from exc
    finally:
        if not enqueued:
            await refund_hourly(limiter, "architecture-diagram", access.user_id)
    response.status_code = 202 if enqueued or status.status in ("queued", "running") else 200
    return DiagramQueueResponse(status=status, enqueued=enqueued)


@router.get("/repositories/{repository_id}/architecture-diagram/status", response_model=DiagramStatus)
async def get_architecture_diagram_status(
    repository_id: int,
    db: AsyncSession = Depends(get_db),
    access: RepoAccess = Depends(read_access),
    _guest_limit: None = Depends(guest_read_limit),
) -> DiagramStatus:
    """Cheap status of the latest attempt and the freshness of the stored result (poll this while queued/running)."""
    try:
        return await store.get_status(db, repository_id)
    except store.RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/repositories/{repository_id}/architecture-diagram", response_model=DiagramResponse)
async def get_architecture_diagram(
    repository_id: int,
    db: AsyncSession = Depends(get_db),
    access: RepoAccess = Depends(read_access),
    _guest_limit: None = Depends(guest_read_limit),
) -> DiagramResponse:
    """The last completed diagram. `status.freshness` is `stale` when the repository was re-ingested, or
    the deterministic analysis re-run, since this diagram was generated. 404 until one has completed."""
    try:
        status, diagram = await store.get_diagram(db, repository_id)
    except store.RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except store.NoResultError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return DiagramResponse(status=status, diagram=diagram)
