"""Ties download -> read -> persist together for one repository ingestion run.

Status handling: `in_progress` is committed *before* the network download, so no transaction or
row lock is held while talking to GitHub. `persist_source_snapshot` flips the status to `completed`
in the same transaction that swaps the files. Any failure is recorded as `failed` with a short,
fixed message in a separate transaction; the previously stored snapshot is never touched on failure.
"""

import asyncio
import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.models.entities import Repository
from apps.api.services.archive_reader import ArchiveReadError, read_archive
from apps.api.services.github import (
    GitHubRateLimitedError,
    GitHubServiceError,
    GitHubTimeoutError,
    GitHubUpstreamError,
    RepositoryNotFoundError,
)
from apps.api.services.github_archive import ArchiveTooLargeError, GitHubArchiveService, MalformedArchiveError
from apps.api.services.source_file_persistence import (
    IncompleteSnapshotError,
    UnknownRepositoryError,
    persist_source_snapshot,
)

logger = logging.getLogger(__name__)


class IngestionInProgressError(Exception):
    """Another ingestion of this repository is already running."""


class IngestionFailedError(Exception):
    """Ingestion failed. `message` is safe to store and show to API clients."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _classify(exc: Exception) -> tuple[int, str]:
    """Map an internal exception to (HTTP status, safe message). Never echoes `str(exc)`."""
    if isinstance(exc, RepositoryNotFoundError):
        return 502, "Repository or archive was not found on GitHub"
    if isinstance(exc, GitHubRateLimitedError):
        return 429, "GitHub rate limit exceeded; try again later"
    if isinstance(exc, GitHubTimeoutError):
        return 504, "Timed out contacting GitHub"
    if isinstance(exc, ArchiveTooLargeError):
        return 422, "Repository archive exceeds the maximum download size"
    if isinstance(exc, (MalformedArchiveError, ArchiveReadError)):
        return 422, "Repository archive could not be read safely"
    if isinstance(exc, IncompleteSnapshotError):
        return 422, "Repository exceeds ingestion limits; the archive was truncated"
    if isinstance(exc, (GitHubUpstreamError, GitHubServiceError)):
        return 502, "GitHub returned an unexpected response"
    return 500, "Unexpected error during ingestion"


async def _claim(db: AsyncSession, repository_id: int) -> str:
    """Atomically move the repository to `in_progress` and return its `owner/repo` name."""
    full_name = (
        await db.execute(
            update(Repository)
            .where(Repository.id == repository_id, Repository.ingestion_status != "in_progress")
            .values(ingestion_status="in_progress", ingestion_error=None)
            .returning(Repository.full_name)
        )
    ).scalar_one_or_none()
    await db.commit()
    if full_name is not None:
        return full_name
    exists = (await db.execute(select(Repository.id).where(Repository.id == repository_id))).scalar_one_or_none()
    if exists is None:
        raise UnknownRepositoryError(f"Repository {repository_id} does not exist.")
    raise IngestionInProgressError(f"Repository {repository_id} is already being ingested.")


async def _record_failure(db: AsyncSession, repository_id: int, message: str) -> None:
    try:
        await db.rollback()
        await db.execute(
            update(Repository)
            .where(Repository.id == repository_id)
            .values(ingestion_status="failed", ingestion_error=message)
        )
        await db.commit()
    except Exception:
        logger.exception("Could not record ingestion failure for repository %s", repository_id)


async def ingest_repository(db: AsyncSession, repository_id: int, archives: GitHubArchiveService) -> int:
    """Download, read and store the repository's latest source snapshot; return files stored.

    Raises `UnknownRepositoryError`, `IngestionInProgressError`, or `IngestionFailedError`.
    """
    full_name = await _claim(db, repository_id)
    try:
        owner, repo = full_name.split("/", 1)
        download = await archives.fetch_repository_archive(owner, repo)
        try:
            result = await asyncio.to_thread(read_archive, download.archive_path, download.commit_sha)
        finally:
            download.cleanup()
        return await persist_source_snapshot(db, repository_id, result)
    except Exception as exc:
        status_code, message = _classify(exc)
        if status_code == 500:
            logger.exception("Ingestion of repository %s failed unexpectedly", repository_id)
        await _record_failure(db, repository_id, message)
        raise IngestionFailedError(status_code, message) from exc
    except BaseException:
        # Cancellation (e.g. client disconnect): don't leave the repository stuck in_progress.
        await _record_failure(db, repository_id, "Ingestion was interrupted")
        raise
