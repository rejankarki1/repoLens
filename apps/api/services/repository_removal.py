"""Removing a repository from a user's workspace.

Repository data (files, chunks, analyses) is shared by every member, so removal has two levels:
- always: the caller's membership and their own conversations about it are deleted;
- only when nobody else holds it and it is not part of the guest showcase: the stored repository itself, whose
  files, chunks, architecture analysis and diagram go with it (foreign keys cascade). Shared embedding vectors are
  content-addressed and are kept for reuse.
Nothing here talks to GitHub: the source repository is never touched.
"""

from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import Settings
from apps.api.models.entities import (
    ArchitectureAnalysis,
    ArchitectureDiagramRecord,
    Conversation,
    Repository,
    UserRepository,
)
from apps.api.services.access import in_showcase
from apps.api.services.embeddings import embedding_job_running


class RemovalBlockedError(Exception):
    """Background work is still running for the repository; the message says what to wait for."""


@dataclass(frozen=True)
class RemovalResult:
    repository_id: int
    data_deleted: bool  # False: other members (or the showcase) still use the stored data


async def _active_jobs(db: AsyncSession, repository: Repository) -> list[str]:
    active = []
    if repository.ingestion_status in ("queued", "in_progress"):
        active.append("ingestion")
    if await embedding_job_running(db, repository.id):
        active.append("indexing")
    for model, label in ((ArchitectureAnalysis, "architecture analysis"), (ArchitectureDiagramRecord, "architecture diagram")):
        status = (await db.execute(select(model.status).where(model.repository_id == repository.id))).scalar_one_or_none()
        if status in ("queued", "running"):
            active.append(label)
    return active


async def remove_from_workspace(db: AsyncSession, repository_id: int, user_id: int, settings: Settings) -> RemovalResult:
    """The caller must already be a member (`member_write`). Commits on success; raises `RemovalBlockedError`."""
    # The row lock serialises with anybody joining the repository (their membership insert needs a share lock on it).
    repository = (
        await db.execute(
            select(Repository).where(Repository.id == repository_id).with_for_update().execution_options(populate_existing=True)
        )
    ).scalar_one()
    others = (
        await db.execute(
            select(func.count()).select_from(UserRepository).where(
                UserRepository.repository_id == repository_id, UserRepository.user_id != user_id
            )
        )
    ).scalar_one()
    data_deleted = others == 0 and not in_showcase(repository, settings)

    if repository.ingestion_status in ("queued", "in_progress"):
        raise RemovalBlockedError("This repository is still being ingested. Remove it once ingestion has finished.")
    if data_deleted:
        active = await _active_jobs(db, repository)
        if active:
            raise RemovalBlockedError(f"Background work is still running for this repository ({', '.join(active)}). Try again when it has finished.")

    await db.execute(delete(Conversation).where(Conversation.repository_id == repository_id, Conversation.user_id == user_id))
    await db.execute(delete(UserRepository).where(UserRepository.repository_id == repository_id, UserRepository.user_id == user_id))
    if data_deleted:
        await db.execute(delete(Repository).where(Repository.id == repository_id))
    await db.commit()
    return RemovalResult(repository_id, data_deleted)
