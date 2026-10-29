"""Repository-scoped architecture graph endpoints (see docs/architecture.md)."""

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from rq import Queue
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.database import get_db
from apps.api.core.queue import enqueue_architecture, get_queue
from apps.api.schemas.architecture import AnalysisStatus, ArchitectureResponse, NodeDetailResponse, QueueResponse
from apps.api.services.architecture import store

router = APIRouter(tags=["architecture"])


@router.post("/repositories/{repository_id}/architecture", response_model=QueueResponse, status_code=202)
async def request_architecture_analysis(
    repository_id: int,
    response: Response,
    force: bool = Query(False, description="Re-analyze even when the stored result is current"),
    db: AsyncSession = Depends(get_db),
    queue: Queue = Depends(get_queue),
) -> QueueResponse:
    """Queue an analysis of the repository's current ingested commit (safe to repeat).

    202 when a job was queued or one is already running; 200 when the stored result is already current.
    """
    try:
        status, enqueued = await store.queue_analysis(
            db, repository_id, lambda: enqueue_architecture(queue, repository_id), force=force
        )
    except store.RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except store.NotIngestedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except store.EnqueueError as exc:
        raise HTTPException(status_code=503, detail="Could not queue the analysis job; please try again") from exc
    response.status_code = 202 if enqueued or status.status in ("queued", "running") else 200
    return QueueResponse(analysis=status, enqueued=enqueued)


@router.get("/repositories/{repository_id}/architecture/status", response_model=AnalysisStatus)
async def get_architecture_status(repository_id: int, db: AsyncSession = Depends(get_db)) -> AnalysisStatus:
    """Cheap status of the latest attempt and the freshness of the stored result (poll this while queued/running)."""
    try:
        return await store.get_status(db, repository_id)
    except store.RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/repositories/{repository_id}/architecture", response_model=ArchitectureResponse)
async def get_architecture(repository_id: int, db: AsyncSession = Depends(get_db)) -> ArchitectureResponse:
    """The high-level view (modules and the dependencies between them) of the last completed analysis.

    The result describes `analysis.analyzed_commit_sha`; `analysis.freshness` is `stale` when the repository has
    been re-ingested since. 404 until an analysis has completed.
    """
    try:
        status, graph = await store.get_overview(db, repository_id)
    except store.RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except store.NoResultError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ArchitectureResponse(analysis=status, graph=graph)


@router.get("/repositories/{repository_id}/architecture/nodes", response_model=NodeDetailResponse)
async def get_architecture_node(
    repository_id: int,
    id: str = Query(min_length=1, max_length=1100, description='A node id from the graph: "module:<dir>" or "file:<path>"'),
    db: AsyncSession = Depends(get_db),
) -> NodeDetailResponse:
    """File-level detail for one module (its files and their dependencies) or one file."""
    try:
        return await store.get_node_detail(db, repository_id, id)
    except (store.RepositoryNotFoundError, store.NoResultError, store.NodeNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
