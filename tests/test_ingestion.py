"""Tests for the enqueue endpoint, the status endpoint, and the worker-side state transitions.

The API side runs against the disposable `repolens_test` database with the RQ queue mocked; the
worker side calls `run_ingestion` directly with a faked GitHub archive service that returns a real
ZIP on disk, so archive reading and PostgreSQL persistence are real.
"""

import io
import zipfile
from datetime import datetime, timezone
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

import apps.api.services.ingestion as ingestion_module
from apps.api.core.queue import get_queue
from apps.api.jobs.ingestion_job import TransientIngestionError, run_ingestion
from apps.api.main import app
from apps.api.models.entities import Repository, SourceFile
from apps.api.services.github import GitHubRateLimitedError
from apps.api.services.github_archive import ArchiveDownloadResult, MalformedArchiveError
from apps.api.services.ingestion import request_ingestion

pytestmark = pytest.mark.anyio

SHA_A = "a" * 40
SHA_B = "b" * 40
_github_ids = iter(range(700_000, 800_000))


def _write_zip(directory: Path, files: dict[str, str], name: str = "archive.zip") -> Path:
    path = directory / name
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for member, content in files.items():
            zf.writestr(f"octo-demo-abc123/{member}", content)
    path.write_bytes(buffer.getvalue())
    return path


@pytest.fixture
def archives() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def queue() -> MagicMock:
    return MagicMock()


@pytest.fixture
async def ingest_client(
    real_client: httpx.AsyncClient, queue: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    # RQ is mocked, so there is never a live RQ job; keeps stale recovery off real Redis.
    monkeypatch.setattr("apps.api.jobs.maintenance.live_repository_ids", lambda _queue: set())
    app.dependency_overrides[get_queue] = lambda: queue
    try:
        yield real_client
    finally:
        app.dependency_overrides.pop(get_queue, None)


async def _make_repository(db: AsyncSession, status: str = "pending") -> int:
    active = status in ("queued", "in_progress")
    repo = Repository(
        github_repo_id=next(_github_ids),
        full_name="octo/demo",
        default_branch="main",
        ingestion_status=status,
        # Active rows carry a fresh heartbeat, as a real queued/running job does.
        ingestion_heartbeat_at=datetime.now(timezone.utc) if active else None,
    )
    db.add(repo)
    await db.commit()
    return repo.id


async def _paths(db: AsyncSession, repository_id: int) -> list[tuple[str, str]]:
    rows = await db.execute(
        select(SourceFile.path, SourceFile.commit_sha)
        .where(SourceFile.repository_id == repository_id)
        .order_by(SourceFile.path)
    )
    return [(path, sha) for path, sha in rows.all()]


async def _repo_state(db: AsyncSession, repository_id: int) -> Repository:
    return (
        await db.execute(
            select(Repository).where(Repository.id == repository_id).execution_options(populate_existing=True)
        )
    ).scalar_one()


def _serve(archives: AsyncMock, path: Path, sha: str) -> None:
    archives.fetch_repository_archive.return_value = ArchiveDownloadResult(archive_path=path, commit_sha=sha)


async def _queue_and_run(
    db: AsyncSession, repository_id: int, archives: AsyncMock, *, will_retry: bool = False
) -> None:
    """What the API (request_ingestion) and the RQ worker (run_ingestion) do, back to back."""
    await request_ingestion(db, repository_id)
    await run_ingestion(db, repository_id, archives, will_retry=will_retry)


# --- POST /repositories/{id}/ingestions ----------------------------------------------------------


async def test_post_enqueues_and_returns_202(
    ingest_client: httpx.AsyncClient, real_db_session: AsyncSession, queue: MagicMock
) -> None:
    repo_id = await _make_repository(real_db_session)

    response = await ingest_client.post(f"/repositories/{repo_id}/ingestions")

    assert response.status_code == 202
    body = response.json()
    assert body["repository_id"] == repo_id
    assert body["status"] == "queued"
    queue.enqueue.assert_called_once()
    assert queue.enqueue.call_args.args[1] == repo_id
    state = await _repo_state(real_db_session, repo_id)
    assert state.ingestion_status == "queued"
    assert state.ingestion_heartbeat_at is not None
    assert state.ingestion_error is None


async def test_post_unknown_repository_returns_404(ingest_client: httpx.AsyncClient, queue: MagicMock) -> None:
    response = await ingest_client.post("/repositories/999999999/ingestions")

    assert response.status_code == 404
    queue.enqueue.assert_not_called()


@pytest.mark.parametrize("active_status", ["queued", "in_progress"])
async def test_post_returns_409_when_ingestion_is_active(
    ingest_client: httpx.AsyncClient, real_db_session: AsyncSession, queue: MagicMock, active_status: str
) -> None:
    repo_id = await _make_repository(real_db_session, status=active_status)

    response = await ingest_client.post(f"/repositories/{repo_id}/ingestions")

    assert response.status_code == 409
    queue.enqueue.assert_not_called()
    assert (await _repo_state(real_db_session, repo_id)).ingestion_status == active_status


async def test_second_post_while_queued_is_rejected_and_enqueues_once(
    ingest_client: httpx.AsyncClient, real_db_session: AsyncSession, queue: MagicMock
) -> None:
    repo_id = await _make_repository(real_db_session)

    first = await ingest_client.post(f"/repositories/{repo_id}/ingestions")
    second = await ingest_client.post(f"/repositories/{repo_id}/ingestions")

    assert (first.status_code, second.status_code) == (202, 409)
    queue.enqueue.assert_called_once()


@pytest.mark.parametrize("previous_status", ["pending", "completed", "failed"])
async def test_post_can_requeue_any_inactive_status(
    ingest_client: httpx.AsyncClient, real_db_session: AsyncSession, previous_status: str
) -> None:
    repo_id = await _make_repository(real_db_session, status=previous_status)

    assert (await ingest_client.post(f"/repositories/{repo_id}/ingestions")).status_code == 202


async def test_redis_failure_returns_503_keeps_snapshot_and_can_be_retried(
    ingest_client: httpx.AsyncClient,
    real_db_session: AsyncSession,
    archives: AsyncMock,
    queue: MagicMock,
    tmp_path: Path,
) -> None:
    repo_id = await _make_repository(real_db_session)
    _serve(archives, _write_zip(tmp_path, {"old.py": "old\n"}), SHA_A)
    await _queue_and_run(real_db_session, repo_id, archives)  # an earlier successful snapshot

    queue.enqueue.side_effect = RedisConnectionError("redis down")
    response = await ingest_client.post(f"/repositories/{repo_id}/ingestions")

    assert response.status_code == 503
    state = await _repo_state(real_db_session, repo_id)
    assert state.ingestion_status == "failed"
    assert state.ingestion_error == "Could not queue the ingestion; please try again"
    assert state.ingestion_heartbeat_at is None
    assert state.last_ingested_commit_sha == SHA_A
    assert await _paths(real_db_session, repo_id) == [("old.py", SHA_A)]

    queue.enqueue.side_effect = None  # Redis is back: the same request now succeeds
    assert (await ingest_client.post(f"/repositories/{repo_id}/ingestions")).status_code == 202
    assert (await _repo_state(real_db_session, repo_id)).ingestion_status == "queued"


# --- GET /repositories/{id}/ingestion-status -----------------------------------------------------


async def test_status_endpoint_reports_queued_then_completed(
    ingest_client: httpx.AsyncClient, real_db_session: AsyncSession, archives: AsyncMock, tmp_path: Path
) -> None:
    repo_id = await _make_repository(real_db_session)
    assert (await ingest_client.get(f"/repositories/{repo_id}/ingestion-status")).json()["status"] == "pending"

    await ingest_client.post(f"/repositories/{repo_id}/ingestions")
    queued = (await ingest_client.get(f"/repositories/{repo_id}/ingestion-status")).json()
    assert queued["status"] == "queued"
    assert queued["commit_sha"] is None

    _serve(archives, _write_zip(tmp_path, {"a.py": "a\n"}), SHA_A)
    await run_ingestion(real_db_session, repo_id, archives)
    done = (await ingest_client.get(f"/repositories/{repo_id}/ingestion-status")).json()
    assert done["status"] == "completed"
    assert done["commit_sha"] == SHA_A
    assert done["last_ingested_at"] is not None
    assert (await ingest_client.get("/repositories/999999999/ingestion-status")).status_code == 404


# --- Worker-side transitions ---------------------------------------------------------------------


async def test_worker_runs_queued_to_in_progress_to_completed(
    real_db_session: AsyncSession, archives: AsyncMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_id = await _make_repository(real_db_session)
    await request_ingestion(real_db_session, repo_id)
    zip_path = _write_zip(tmp_path, {"src/app.py": "x = 1\n", "README.md": "# hi\n"})
    _serve(archives, zip_path, SHA_A)
    real_persist = ingestion_module.persist_source_snapshot
    seen: list[str] = []

    async def spy(db: AsyncSession, repository_id: int, result: object, chunks: object = None) -> int:
        state = await _repo_state(db, repository_id)
        seen.append(state.ingestion_status)
        assert state.ingestion_heartbeat_at is not None  # set by the worker's claim
        return await real_persist(db, repository_id, result, chunks)  # type: ignore[arg-type]

    monkeypatch.setattr(ingestion_module, "persist_source_snapshot", spy)

    await run_ingestion(real_db_session, repo_id, archives)

    assert seen == ["in_progress"]
    state = await _repo_state(real_db_session, repo_id)
    assert (state.ingestion_status, state.last_ingested_commit_sha) == ("completed", SHA_A)
    assert state.ingestion_heartbeat_at is None
    assert await _paths(real_db_session, repo_id) == [("README.md", SHA_A), ("src/app.py", SHA_A)]
    archives.fetch_repository_archive.assert_awaited_once_with("octo", "demo")
    assert not zip_path.exists()


async def test_worker_records_failure_and_cleans_up_unreadable_archive(
    real_db_session: AsyncSession, archives: AsyncMock, tmp_path: Path
) -> None:
    repo_id = await _make_repository(real_db_session)
    await request_ingestion(real_db_session, repo_id)
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    _serve(archives, bad, SHA_A)

    await run_ingestion(real_db_session, repo_id, archives)  # permanent failure: returns, no retry

    state = await _repo_state(real_db_session, repo_id)
    assert state.ingestion_status == "failed"
    assert state.ingestion_error == "Repository archive could not be read safely"
    assert state.ingestion_heartbeat_at is None
    assert not bad.exists()
    assert await _paths(real_db_session, repo_id) == []


async def test_worker_failure_after_success_keeps_previous_snapshot(
    real_db_session: AsyncSession, archives: AsyncMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_id = await _make_repository(real_db_session)
    _serve(archives, _write_zip(tmp_path, {"old.py": "old\n"}, "one.zip"), SHA_A)
    await _queue_and_run(real_db_session, repo_id, archives)

    async def delete_then_explode(db: AsyncSession, repository_id: int, result: object, chunks: object = None) -> int:
        await db.execute(delete(SourceFile).where(SourceFile.repository_id == repository_id))
        raise RuntimeError("disk exploded at /secret/path")

    monkeypatch.setattr(ingestion_module, "persist_source_snapshot", delete_then_explode)
    second_zip = _write_zip(tmp_path, {"new.py": "new\n"}, "two.zip")
    _serve(archives, second_zip, SHA_B)

    await _queue_and_run(real_db_session, repo_id, archives)

    state = await _repo_state(real_db_session, repo_id)
    assert state.ingestion_status == "failed"
    assert state.ingestion_error == "Unexpected error during ingestion"
    assert state.last_ingested_commit_sha == SHA_A
    assert await _paths(real_db_session, repo_id) == [("old.py", SHA_A)]
    assert not second_zip.exists()


async def test_truncated_archive_is_rejected_and_marked_failed(
    real_db_session: AsyncSession, archives: AsyncMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_id = await _make_repository(real_db_session)
    _serve(archives, _write_zip(tmp_path, {f"f{i}.py": "x\n" for i in range(3)}), SHA_A)
    real_read = ingestion_module.read_archive

    def tiny_limit(path: Path, sha: str):  # type: ignore[no-untyped-def]
        from apps.api.config import Settings

        return real_read(path, sha, settings=Settings(archive_reader_max_files_accepted=1))

    monkeypatch.setattr(ingestion_module, "read_archive", tiny_limit)

    await _queue_and_run(real_db_session, repo_id, archives)

    state = await _repo_state(real_db_session, repo_id)
    assert state.ingestion_status == "failed"
    assert "truncated" in state.ingestion_error
    assert await _paths(real_db_session, repo_id) == []


async def test_transient_failure_requeues_then_retry_completes(
    real_db_session: AsyncSession, archives: AsyncMock, tmp_path: Path
) -> None:
    repo_id = await _make_repository(real_db_session)
    await request_ingestion(real_db_session, repo_id)
    archives.fetch_repository_archive.side_effect = GitHubRateLimitedError("slow down")

    with pytest.raises(TransientIngestionError):
        await run_ingestion(real_db_session, repo_id, archives, will_retry=True)

    state = await _repo_state(real_db_session, repo_id)
    assert state.ingestion_status == "queued"  # waiting for RQ's scheduled retry, which claims `queued`
    assert state.ingestion_heartbeat_at is not None

    archives.fetch_repository_archive.side_effect = None
    _serve(archives, _write_zip(tmp_path, {"a.py": "a\n"}), SHA_A)
    await run_ingestion(real_db_session, repo_id, archives, will_retry=False)
    assert (await _repo_state(real_db_session, repo_id)).ingestion_status == "completed"


async def test_transient_failure_without_retries_left_ends_failed(
    real_db_session: AsyncSession, archives: AsyncMock
) -> None:
    repo_id = await _make_repository(real_db_session)
    await request_ingestion(real_db_session, repo_id)
    archives.fetch_repository_archive.side_effect = GitHubRateLimitedError("slow down")

    with pytest.raises(TransientIngestionError):
        await run_ingestion(real_db_session, repo_id, archives, will_retry=False)

    state = await _repo_state(real_db_session, repo_id)
    assert state.ingestion_status == "failed"
    assert state.ingestion_error == "GitHub rate limit exceeded; try again later"


async def test_malformed_download_from_github_service_is_a_permanent_failure(
    real_db_session: AsyncSession, archives: AsyncMock
) -> None:
    repo_id = await _make_repository(real_db_session)
    await request_ingestion(real_db_session, repo_id)
    archives.fetch_repository_archive.side_effect = MalformedArchiveError("not a zip")

    await run_ingestion(real_db_session, repo_id, archives, will_retry=True)  # no raise: not retryable

    assert (await _repo_state(real_db_session, repo_id)).ingestion_status == "failed"
