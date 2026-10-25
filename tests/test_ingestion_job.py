"""Tests for the RQ ingestion job, queue configuration, and worker selection.

Job logic runs against the disposable `repolens_test` database with a faked GitHub archive service.
Queue tests use a uniquely named queue in Redis DB 15 and delete only what they created.
"""

import io
import uuid
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from redis import Redis
from redis.exceptions import RedisError
from rq import Queue
from rq.worker import SpawnWorker, Worker
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import apps.api.jobs.ingestion_job as job_module
import apps.api.worker as worker_module
from apps.api.config import Settings, get_settings
from apps.api.core.queue import enqueue_ingestion, get_ingestion_queue
from apps.api.jobs.ingestion_job import TransientIngestionError, ingest_repository_job, run_ingestion
from apps.api.models.entities import Repository, SourceFile
from apps.api.services.github import GitHubRateLimitedError, GitHubTimeoutError, GitHubUpstreamError
from apps.api.services.github_archive import ArchiveDownloadResult, MalformedArchiveError

SHA = "c" * 40
_github_ids = iter(range(500_000, 600_000))


async def _make_repository(db: AsyncSession, *, status: str = "queued") -> int:
    repo = Repository(
        github_repo_id=next(_github_ids), full_name="octo/demo", default_branch="main", ingestion_status=status
    )
    db.add(repo)
    await db.commit()
    return repo.id


def _zip(tmp_path: Path, files: dict[str, str]) -> Path:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in files.items():
            zf.writestr(f"octo-demo-abc/{name}", content)
    path = tmp_path / "a.zip"
    path.write_bytes(buffer.getvalue())
    return path


async def _status(db: AsyncSession, repository_id: int) -> Repository:
    return (
        await db.execute(select(Repository).where(Repository.id == repository_id).execution_options(populate_existing=True))
    ).scalar_one()


@pytest.mark.anyio
async def test_successful_job_stores_files_and_completes(real_db_session: AsyncSession, tmp_path: Path) -> None:
    repo_id = await _make_repository(real_db_session)
    archives = AsyncMock()
    path = _zip(tmp_path, {"src/app.py": "x = 1\n"})
    archives.fetch_repository_archive.return_value = ArchiveDownloadResult(archive_path=path, commit_sha=SHA)

    await run_ingestion(real_db_session, repo_id, archives)

    repo = await _status(real_db_session, repo_id)
    assert (repo.ingestion_status, repo.last_ingested_commit_sha) == ("completed", SHA)
    paths = (await real_db_session.execute(select(SourceFile.path).where(SourceFile.repository_id == repo_id))).scalars().all()
    assert paths == ["src/app.py"]
    assert not path.exists()


@pytest.mark.anyio
async def test_permanent_failure_is_recorded_and_not_retried(real_db_session: AsyncSession, tmp_path: Path) -> None:
    repo_id = await _make_repository(real_db_session)
    archives = AsyncMock()
    archives.fetch_repository_archive.side_effect = MalformedArchiveError("not a zip")

    await run_ingestion(real_db_session, repo_id, archives)  # returns normally: RQ must not retry

    repo = await _status(real_db_session, repo_id)
    assert repo.ingestion_status == "failed"
    assert repo.ingestion_error == "Repository archive could not be read safely"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error", [GitHubTimeoutError("t"), GitHubRateLimitedError("r"), GitHubUpstreamError("u")]
)
async def test_transient_failure_raises_for_rq_retry(real_db_session: AsyncSession, error: Exception) -> None:
    repo_id = await _make_repository(real_db_session)
    archives = AsyncMock()
    archives.fetch_repository_archive.side_effect = error

    with pytest.raises(TransientIngestionError):
        await run_ingestion(real_db_session, repo_id, archives)

    assert (await _status(real_db_session, repo_id)).ingestion_status == "failed"


@pytest.mark.anyio
async def test_unknown_repository_is_skipped_without_retry(real_db_session: AsyncSession) -> None:
    archives = AsyncMock()

    await run_ingestion(real_db_session, 999_999_999, archives)

    archives.fetch_repository_archive.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["in_progress", "pending", "completed"])
async def test_job_is_skipped_when_repository_is_not_queued(real_db_session: AsyncSession, status: str) -> None:
    repo_id = await _make_repository(real_db_session, status=status)
    archives = AsyncMock()

    await run_ingestion(real_db_session, repo_id, archives)  # duplicate / stale delivery: no-op

    archives.fetch_repository_archive.assert_not_awaited()
    assert (await _status(real_db_session, repo_id)).ingestion_status == status


def test_sync_entry_point_runs_the_async_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    run = AsyncMock()
    monkeypatch.setattr(job_module, "_run", run)

    ingest_repository_job(42)

    run.assert_awaited_once_with(42, will_retry=False)


def test_worker_class_depends_on_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker_module.sys, "platform", "darwin")
    assert worker_module.worker_class() is SpawnWorker
    monkeypatch.setattr(worker_module.sys, "platform", "linux")
    assert worker_module.worker_class() is Worker


@pytest.fixture
def test_queue() -> Queue:
    settings = get_settings()
    connection = Redis.from_url(settings.redis_url, db=15)
    try:
        connection.ping()
    except RedisError:
        pytest.skip("Redis is not available")
    queue = Queue(f"repolens-test-{uuid.uuid4().hex}", connection=connection)
    yield queue
    queue.empty()
    queue.delete()
    connection.close()


def test_enqueue_applies_timeout_and_retry_policy(test_queue: Queue) -> None:
    job = enqueue_ingestion(test_queue, 7, Settings())

    try:
        assert job.func_name == "apps.api.jobs.ingestion_job.ingest_repository_job"
        assert job.args == (7,)
        assert job.timeout == 900
        assert job.retries_left == 2
        assert job.retry_intervals == [30, 120]
        assert job.id in test_queue.job_ids
    finally:
        job.delete()


def test_default_queue_uses_configured_name() -> None:
    queue = get_ingestion_queue(connection=Redis(), settings=Settings(ingestion_queue_name="custom"))

    assert queue.name == "custom"
