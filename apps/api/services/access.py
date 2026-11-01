"""Who may touch which repository. Repository data is shared; workspaces and conversations are personal.

- member: the repository is in the signed-in user's workspace (full use, subject to quotas).
- guest: the repository is in the explicit showcase allow-list (`GUEST_SHOWCASE_REPOSITORIES`): read-only browsing,
  for signed-out visitors and for signed-in users who have not added it.
Everything else is invisible: 404 for a signed-in user, 401 for a visitor, so ids cannot be probed.
"""

from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import Settings
from apps.api.core.auth import Principal, get_principal, require_csrf, require_user, settings_dep
from apps.api.core.database import get_db
from apps.api.models.entities import Repository, UserRepository

Kind = Literal["member", "guest"]


@dataclass(frozen=True)
class RepoAccess:
    repository: Repository
    kind: Kind
    principal: Principal

    @property
    def user_id(self) -> int:
        assert self.principal.user is not None, "member access always has a user"
        return self.principal.user.id


def showcase_names(settings: Settings) -> frozenset[str]:
    return frozenset(name.strip().lower() for name in settings.guest_showcase_repositories.split(",") if name.strip())


def in_showcase(repository: Repository, settings: Settings) -> bool:
    return repository.full_name.lower() in showcase_names(settings)


async def is_member(db: AsyncSession, user_id: int, repository_id: int) -> bool:
    row = await db.execute(
        select(UserRepository.user_id).where(UserRepository.user_id == user_id, UserRepository.repository_id == repository_id)
    )
    return row.first() is not None


async def add_member(db: AsyncSession, user_id: int, repository_id: int) -> None:
    """Idempotent, and safe when two requests add the same repository at once."""
    await db.execute(
        pg_insert(UserRepository).values(user_id=user_id, repository_id=repository_id).on_conflict_do_nothing()
    )


async def resolve_access(
    repository_id: int, principal: Principal, db: AsyncSession, settings: Settings, *, member_only: bool
) -> RepoAccess:
    repository = await db.get(Repository, repository_id)
    showcased = repository is not None and in_showcase(repository, settings)
    if principal.user is None:
        if showcased and not member_only:
            return RepoAccess(repository, "guest", principal)  # type: ignore[arg-type]
        raise HTTPException(status_code=401, detail="Sign in with GitHub to continue")
    if repository is not None and await is_member(db, principal.user.id, repository_id):
        return RepoAccess(repository, "member", principal)
    if repository is not None and showcased:
        if member_only:
            raise HTTPException(status_code=403, detail="Add this repository to your repositories to use this feature")
        return RepoAccess(repository, "guest", principal)
    raise HTTPException(status_code=404, detail=f"Repository {repository_id} not found")


async def read_access(
    repository_id: int,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> RepoAccess:
    """Members and, for showcase repositories, guests: read-only endpoints."""
    return await resolve_access(repository_id, principal, db, settings, member_only=False)


async def member_read(
    repository_id: int,
    principal: Principal = Depends(require_user),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> RepoAccess:
    """Members only, for reads that spend resources or expose personal data."""
    return await resolve_access(repository_id, principal, db, settings, member_only=True)


async def member_write(
    repository_id: int,
    principal: Principal = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> RepoAccess:
    """Members only, with the CSRF token: anything that changes state."""
    return await resolve_access(repository_id, principal, db, settings, member_only=True)
