"""Repository read and write endpoints."""

import logging

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.database import get_db
from apps.api.config import Settings
from apps.api.core.auth import Principal, get_principal, require_csrf, settings_dep
from apps.api.models.entities import Repository, UserRepository
from apps.api.schemas.repository import RepositoryCreate, RepositoryResponse
from apps.api.services.access import RepoAccess, add_member, read_access, showcase_names
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


def _with_access(repository: Repository, kind: str) -> RepositoryResponse:
    return RepositoryResponse.model_validate(repository).model_copy(update={"viewer_access": kind})


@router.get("/repositories", response_model=list[RepositoryResponse], status_code=200)
async def list_repositories(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
    settings: Settings = Depends(settings_dep),
) -> list[RepositoryResponse]:
    """The signed-in user's workspace; for a visitor, only the explicitly approved showcase. In ID order."""
    if principal.user is not None:
        query = (
            select(Repository)
            .join(UserRepository, UserRepository.repository_id == Repository.id)
            .where(UserRepository.user_id == principal.user.id)
            .order_by(Repository.id)
        )
        return [_with_access(repo, "member") for repo in (await db.execute(query)).scalars().all()]
    names = showcase_names(settings)
    if not names:
        return []
    query = select(Repository).where(func.lower(Repository.full_name).in_(names)).order_by(Repository.id)
    return [_with_access(repo, "guest") for repo in (await db.execute(query)).scalars().all()]


@router.get("/repositories/{repository_id}", response_model=RepositoryResponse, status_code=200)
async def get_repository(repository_id: int, access: RepoAccess = Depends(read_access)) -> RepositoryResponse:
    """Fetch a single repository by its internal ID (members, and guests for showcase repositories)."""
    return _with_access(access.repository, access.kind)


@router.post("/repositories", response_model=RepositoryResponse, status_code=201)
async def create_repository(
    payload: RepositoryCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
    github: GitHubService = Depends(get_github_service),
    principal: Principal = Depends(require_csrf),
) -> RepositoryResponse:
    """Add a public GitHub repository to the signed-in user's workspace.

    A repository somebody already imported is reused (200, nothing is downloaded again); otherwise a new record is
    created (201) and the client starts ingestion. Either way the user becomes a member.
    """
    assert principal.user is not None
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

    existing = (
        await db.execute(select(Repository).where(Repository.github_repo_id == metadata.github_repo_id))
    ).scalar_one_or_none()
    created = existing is None
    repository = existing or Repository(
        github_repo_id=metadata.github_repo_id, full_name=metadata.full_name, default_branch=metadata.default_branch
    )
    if created:
        db.add(repository)
    try:
        await db.flush()
        await add_member(db, principal.user.id, repository.id)
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        if isinstance(exc, IntegrityError) and isinstance(exc.orig, asyncpg.exceptions.UniqueViolationError):
            # Two people imported the same repository at the same moment; the other request won.
            repository = (
                await db.execute(select(Repository).where(Repository.github_repo_id == metadata.github_repo_id))
            ).scalar_one()
            await add_member(db, principal.user.id, repository.id)
            await db.commit()
            created = False
        else:
            logger.exception("Failed to save repository %s", metadata.full_name)
            raise HTTPException(status_code=500, detail="Failed to save repository") from exc

    await db.refresh(repository)
    response.status_code = 201 if created else 200
    return _with_access(repository, "member")
