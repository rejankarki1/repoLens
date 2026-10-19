"""Repository read and write endpoints."""

import logging

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.database import get_db
from apps.api.models.entities import Repository
from apps.api.schemas.repository import RepositoryCreate, RepositoryResponse
from apps.api.services.github import (
    GitHubRateLimitedError,
    GitHubService,
    GitHubTimeoutError,
    GitHubUpstreamError,
    InvalidRepositoryURLError,
    RepositoryNotFoundError,
    get_github_service,
)


logger = logging.getLogger(__name__)
router = APIRouter(tags=["repositories"])


@router.get("/repositories", response_model=list[RepositoryResponse], status_code=200)
async def list_repositories(db: AsyncSession = Depends(get_db)) -> list[RepositoryResponse]:
    """List repositories in ID order."""
    result = await db.execute(select(Repository).order_by(Repository.id))
    return [RepositoryResponse.model_validate(repo) for repo in result.scalars().all()]


@router.get("/repositories/{repository_id}", response_model=RepositoryResponse, status_code=200)
async def get_repository(repository_id: int, db: AsyncSession = Depends(get_db)) -> RepositoryResponse:
    """Fetch a single repository by its internal ID."""
    result = await db.execute(select(Repository).where(Repository.id == repository_id))
    repository = result.scalar_one_or_none()
    if repository is None:
        raise HTTPException(status_code=404, detail=f"Repository {repository_id} not found")
    return RepositoryResponse.model_validate(repository)


@router.post("/repositories", response_model=RepositoryResponse, status_code=201)
async def create_repository(
    payload: RepositoryCreate,
    db: AsyncSession = Depends(get_db),
    github: GitHubService = Depends(get_github_service),
) -> RepositoryResponse:
    """Import a public GitHub repository by URL."""
    try:
        metadata = await github.get_repository_metadata(payload.github_url)
    except InvalidRepositoryURLError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GitHubRateLimitedError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except GitHubTimeoutError as exc:
        raise HTTPException(status_code=504, detail="Timed out contacting GitHub") from exc
    except GitHubUpstreamError as exc:
        raise HTTPException(status_code=502, detail="GitHub API returned an unexpected error") from exc

    existing = await db.execute(select(Repository).where(Repository.github_repo_id == metadata.github_repo_id))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail=f"Repository {metadata.full_name} is already imported")

    repository = Repository(
        github_repo_id=metadata.github_repo_id,
        full_name=metadata.full_name,
        default_branch=metadata.default_branch,
    )
    db.add(repository)
    try:
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        if isinstance(exc, IntegrityError) and isinstance(exc.orig, asyncpg.exceptions.UniqueViolationError):
            raise HTTPException(
                status_code=409, detail=f"Repository {metadata.full_name} is already imported"
            ) from exc
        logger.exception("Failed to save repository %s", metadata.full_name)
        raise HTTPException(status_code=500, detail="Failed to save repository") from exc

    await db.refresh(repository)
    return RepositoryResponse.model_validate(repository)
