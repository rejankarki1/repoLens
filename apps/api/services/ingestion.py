"""Ingestion state machine and pipeline, shared by the API (enqueue) and the RQ worker (run).

PostgreSQL is the source of truth for state:

    pending/completed/failed --request_ingestion--> queued --claim_queued--> in_progress
    in_progress --persist_source_snapshot--> completed
    in_progress --_record_failure--> failed            (previous snapshot untouched)
    in_progress --_requeue_for_retry--> queued         (transient failure, RQ retry pending)
    queued --abort_enqueue--> failed                   (Redis refused the job)

Every transition is a single conditional UPDATE, and none is held open across the GitHub download.
"""

import asyncio
import logging
import time
from collections.abc import Collection
from datetime import timedelta

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import get_settings
from apps.api.models.entities import Repository
from apps.api.services.archive_reader import ArchiveReadError, read_archive
from apps.api.services.code_indexing import index_entries
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

_ACTIVE_STATUSES = ("queued", "in_progress")
# HTTP-style codes from `_classify` that are worth retrying: rate limit, bad gateway, timeout.
_TRANSIENT_STATUS_CODES = frozenset({429, 502, 504})


class IngestionInProgressError(Exception):
    """An ingestion of this repository is already queued or running (API-side 409)."""


class IngestionNotQueuedError(Exception):
    """The repository is not `queued`: a duplicate or stale job delivery (worker-side skip)."""


class IngestionFailedError(Exception):
    """Ingestion failed. `message` is safe to store and show to API clients."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message

    @property
    def retryable(self) -> bool:
        return self.status_code in _TRANSIENT_STATUS_CODES


def _classify(exc: Exception) -> tuple[int, str]:
    """Map an internal exception to (HTTP-style status, safe message). Never echoes `str(exc)`."""
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


async def _ensure_exists(db: AsyncSession, repository_id: int) -> None:
    exists = (await db.execute(select(Repository.id).where(Repository.id == repository_id))).scalar_one_or_none()
    if exists is None:
        raise UnknownRepositoryError(f"Repository {repository_id} does not exist.")


# --- API side ------------------------------------------------------------------------------------


async def request_ingestion(db: AsyncSession, repository_id: int) -> None:
    """Atomically move the repository to `queued`. The caller must then enqueue the RQ job.

    Raises `UnknownRepositoryError` or, if another ingestion is queued/running, `IngestionInProgressError`.
    Only one of any number of concurrent requests wins this UPDATE.
    """
    claimed = (
        await db.execute(
            update(Repository)
            .where(Repository.id == repository_id, Repository.ingestion_status.not_in(_ACTIVE_STATUSES))
            .values(ingestion_status="queued", ingestion_error=None, ingestion_heartbeat_at=func.now())
            .returning(Repository.id)
        )
    ).scalar_one_or_none()
    await db.commit()
    if claimed is None:
        await _ensure_exists(db, repository_id)
        raise IngestionInProgressError(f"Repository {repository_id} is already queued or being ingested.")


async def abort_enqueue(db: AsyncSession, repository_id: int, message: str) -> None:
    """Undo `request_ingestion` when the job could not be enqueued, so the request can be retried.

    Touches only the status fields, and only while still `queued`: a previously stored snapshot
    (`last_ingested_*`, `source_files`) is never modified. `failed` is not an active status, so
    the next request can claim again.
    """
    await db.execute(
        update(Repository)
        .where(Repository.id == repository_id, Repository.ingestion_status == "queued")
        .values(ingestion_status="failed", ingestion_error=message, ingestion_heartbeat_at=None)
    )
    await db.commit()


# --- Worker side ---------------------------------------------------------------------------------


async def claim_queued(db: AsyncSession, repository_id: int) -> str:
    """Worker claim: `queued` -> `in_progress`. Returns the repository's `owner/repo` name."""
    full_name = (
        await db.execute(
            update(Repository)
            .where(Repository.id == repository_id, Repository.ingestion_status == "queued")
            .values(ingestion_status="in_progress", ingestion_heartbeat_at=func.now())
            .returning(Repository.full_name)
        )
    ).scalar_one_or_none()
    await db.commit()
    if full_name is None:
        await _ensure_exists(db, repository_id)
        raise IngestionNotQueuedError(f"Repository {repository_id} is not queued.")
    return full_name


async def _record_failure(db: AsyncSession, repository_id: int, message: str) -> None:
    await _set_status_after_failure(db, repository_id, "failed", message)


async def _requeue_for_retry(db: AsyncSession, repository_id: int, message: str) -> None:
    """Put the repository back to `queued` so the RQ retry (which claims `queued`) can run."""
    await _set_status_after_failure(db, repository_id, "queued", message, heartbeat=func.now())


async def _set_status_after_failure(
    db: AsyncSession, repository_id: int, status: str, message: str, heartbeat: object = None
) -> None:
    try:
        await db.rollback()
        await db.execute(
            update(Repository)
            .where(Repository.id == repository_id)
            .values(ingestion_status=status, ingestion_error=message, ingestion_heartbeat_at=heartbeat)
        )
        await db.commit()
    except Exception:
        logger.exception("Could not record ingestion state %r for repository %s", status, repository_id)


async def touch_heartbeat(db: AsyncSession, repository_id: int) -> bool:
    """Refresh the heartbeat of a repository that is `in_progress`; returns whether a row was updated.

    The status condition means a heartbeat can never revive a row that already finished or was
    recovered. The short lock timeout skips a tick instead of queueing behind the long
    `SELECT ... FOR UPDATE` that `persist_source_snapshot` holds while swapping files.
    """
    await db.execute(text("SET LOCAL lock_timeout = '2s'"))
    result = await db.execute(
        update(Repository)
        .where(Repository.id == repository_id, Repository.ingestion_status == "in_progress")
        .values(ingestion_heartbeat_at=func.now())
    )
    await db.commit()
    return result.rowcount > 0


def _stale_condition(running_after_seconds: int, queued_after_seconds: int):  # type: ignore[no-untyped-def]
    heartbeat = Repository.ingestion_heartbeat_at
    return or_(
        and_(
            Repository.ingestion_status == "in_progress",
            or_(heartbeat.is_(None), heartbeat < func.now() - timedelta(seconds=running_after_seconds)),
        ),
        and_(
            Repository.ingestion_status == "queued",
            or_(heartbeat.is_(None), heartbeat < func.now() - timedelta(seconds=queued_after_seconds)),
        ),
    )


async def recover_stale_ingestions(
    db: AsyncSession,
    live_repository_ids: Collection[int],
    repository_id: int | None = None,
) -> list[int]:
    """Mark stuck ingestions `failed` and return their repository IDs.

    A row is stale when it is `in_progress` (or `queued`) and its heartbeat is older than the
    configured threshold. `live_repository_ids` are repositories RQ still has a live job for
    (waiting, scheduled for retry, or running on a registered worker); those are never touched, so
    a slow-but-alive job is not recovered. It is one conditional UPDATE, so a heartbeat that lands
    first wins. Only status fields change: the stored snapshot and `last_ingested_*` are untouched,
    and the user (or the next POST) can start a new ingestion because `failed` is not active.
    """
    settings = get_settings()
    conditions = [_stale_condition(settings.ingestion_stale_running_seconds, settings.ingestion_stale_queued_seconds)]
    if live_repository_ids:
        conditions.append(Repository.id.not_in(list(live_repository_ids)))
    if repository_id is not None:
        conditions.append(Repository.id == repository_id)
    rows = await db.execute(
        update(Repository)
        .where(*conditions)
        .values(
            ingestion_status="failed",
            ingestion_error="Ingestion was interrupted; please try again",
            ingestion_heartbeat_at=None,
        )
        .returning(Repository.id)
    )
    recovered = list(rows.scalars())
    await db.commit()
    if recovered:
        logger.warning("Recovered stale ingestions for repositories %s", recovered)
    return recovered


async def ingest_repository(
    db: AsyncSession, repository_id: int, archives: GitHubArchiveService, *, will_retry: bool = False
) -> int:
    """Claim a queued repository, then download, read and store its latest source snapshot.

    Returns the number of files stored. Raises `UnknownRepositoryError`, `IngestionNotQueuedError`
    or `IngestionFailedError`. No transaction is open during the download. `will_retry` says whether
    RQ will retry after a transient failure; if so the repository goes back to `queued` instead of
    `failed`.
    """
    full_name = await claim_queued(db, repository_id)
    try:
        owner, repo = full_name.split("/", 1)
        download = await archives.fetch_repository_archive(owner, repo)
        try:
            result = await asyncio.to_thread(read_archive, download.archive_path, download.commit_sha)
        finally:
            download.cleanup()
        # Chunk the files before the persistence transaction starts, in a thread so the event loop (and
        # the job's heartbeat) keeps running. A truncated result is rejected by the persistence step.
        index = None if result.truncated else await asyncio.to_thread(index_entries, result.entries)
        persist_started = time.perf_counter()
        files_stored = await persist_source_snapshot(
            db, repository_id, result, index.chunks_by_path if index else None
        )
        if index is not None:
            logger.info(
                "Indexed repository %s at %s: %s files, %s chunks (%s AST files, %s text-fallback files %s, %s unchunked); "
                "chunking %.0f ms, database write %.0f ms",
                repository_id, result.commit_sha[:7], index.stats.files, index.stats.chunks, index.stats.ast_files,
                index.stats.fallback_files, index.stats.fallback_reasons, index.stats.failed_files,
                index.stats.duration_ms, (time.perf_counter() - persist_started) * 1000,
            )
        return files_stored
    except Exception as exc:
        status_code, message = _classify(exc)
        if status_code == 500:
            logger.exception("Ingestion of repository %s failed unexpectedly", repository_id)
        failure = IngestionFailedError(status_code, message)
        if failure.retryable and will_retry:
            await _requeue_for_retry(db, repository_id, message)
        else:
            await _record_failure(db, repository_id, message)
        raise failure from exc
    except BaseException:
        # Cancellation (e.g. worker shutdown): don't leave the repository stuck in_progress.
        await _record_failure(db, repository_id, "Ingestion was interrupted")
        raise
