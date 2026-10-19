"""Repository ingestion endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.database import get_db
from apps.api.models.entities import Repository
from apps.api.schemas.ingestion import IngestionResultResponse, IngestionStatusResponse
from apps.api.services.github_archive import GitHubArchiveService, get_archive_service
from apps.api.services.ingestion import IngestionFailedError, IngestionInProgressError, ingest_repository
from apps.api.services.source_file_persistence import UnknownRepositoryError

router = APIRouter(tags=["ingestion"])


def _status_response(repository: Repository) -> dict[str, object]:
    return {
        "repository_id": repository.id,
        "status": repository.ingestion_status,
        "commit_sha": repository.last_ingested_commit_sha,
        "truncated": repository.ingestion_truncated,
        "last_ingested_at": repository.last_ingested_at,
        "error": repository.ingestion_error,
    }


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
    response_model=IngestionResultResponse,
    status_code=200,
)
async def create_ingestion(
    repository_id: int,
    db: AsyncSession = Depends(get_db),
    archives: GitHubArchiveService = Depends(get_archive_service),
) -> IngestionResultResponse:
    """Download, filter and store the repository's latest source code. Returns when storage is done."""
    try:
        files_stored = await ingest_repository(db, repository_id, archives)
    except UnknownRepositoryError as exc:
        raise HTTPException(status_code=404, detail=f"Repository {repository_id} not found") from exc
    except IngestionInProgressError as exc:
        raise HTTPException(status_code=409, detail="An ingestion for this repository is already in progress") from exc
    except IngestionFailedError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    repository = await _get_repository_or_404(db, repository_id)
    return IngestionResultResponse(**_status_response(repository), files_stored=files_stored)


@router.get(
    "/repositories/{repository_id}/ingestion-status",
    response_model=IngestionStatusResponse,
    status_code=200,
)
async def get_ingestion_status(repository_id: int, db: AsyncSession = Depends(get_db)) -> IngestionStatusResponse:
    """Current ingestion state and the commit of the stored snapshot."""
    repository = await _get_repository_or_404(db, repository_id)
    return IngestionStatusResponse(**_status_response(repository))
