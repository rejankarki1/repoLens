"""Saves an `ArchiveReadResult` into PostgreSQL as the repository's latest source-file snapshot.

Storage only: it does not download archives, read ZIPs, or know about HTTP. The caller supplies a
repository ID and an already-read result.
"""

from datetime import datetime, timezone

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.models.entities import Repository, SourceFile
from apps.api.services.archive_reader import ArchiveReadResult


class SourcePersistenceError(Exception):
    """Base class for errors raised by this module."""


class UnknownRepositoryError(SourcePersistenceError):
    """No `repositories` row exists for the given ID."""


class IncompleteSnapshotError(SourcePersistenceError):
    """The archive result was truncated, so it is not a complete picture of the repository."""


async def persist_source_snapshot(
    db: AsyncSession, repository_id: int, result: ArchiveReadResult
) -> int:
    """Replace the repository's stored snapshot with `result` and return the number of files saved.

    Everything happens in one transaction that this function commits. If anything fails, the
    transaction is rolled back and the previously stored snapshot (and its ingestion metadata)
    is left exactly as it was. Truncated results are rejected before the database is touched.

    The repository row is locked (`SELECT ... FOR UPDATE`) for the duration, so concurrent
    ingestions of the same repository run one after another; the last one to commit wins.
    Old rows are deleted before the new ones are inserted, so re-ingesting the same commit
    cannot create duplicates and no rows from a previous commit survive.
    """
    if result.truncated:
        raise IncompleteSnapshotError(
            f"Archive for {result.commit_sha} was truncated ({result.limit_hit}); "
            "refusing to store a partial snapshot."
        )

    try:
        repository = (
            await db.execute(
                select(Repository)
                .where(Repository.id == repository_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if repository is None:
            raise UnknownRepositoryError(f"Repository {repository_id} does not exist.")

        await db.execute(delete(SourceFile).where(SourceFile.repository_id == repository_id))
        if result.entries:
            await db.execute(
                insert(SourceFile),
                [
                    {
                        "repository_id": repository_id,
                        "commit_sha": result.commit_sha,
                        "path": entry.path,
                        "language": entry.language,
                        "category": entry.category.value,
                        "content": entry.content,
                        "content_hash": entry.sha256,
                        "size_bytes": entry.size_bytes,
                        "line_count": entry.line_count,
                    }
                    for entry in result.entries
                ],
            )

        repository.last_ingested_commit_sha = result.commit_sha
        repository.ingestion_status = "completed"
        repository.ingestion_truncated = False
        repository.ingestion_error = None
        repository.last_ingested_at = datetime.now(timezone.utc)
        repository.ingestion_heartbeat_at = None
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    return len(result.entries)
