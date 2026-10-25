"""Heartbeat, stale-job detection/recovery, snapshot preservation, and orphan temp-file cleanup."""

import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from redis import Redis
from redis.exceptions import RedisError
from rq import Queue
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import get_settings
from apps.api.core.queue import _worker_is_alive, enqueue_ingestion, live_repository_ids
from apps.api.jobs.ingestion_job import heartbeat_loop
from apps.api.jobs.maintenance import ARCHIVE_PREFIX, cleanup_orphan_archives, sweep_stale_ingestions
from apps.api.main import app
from apps.api.models.entities import Repository, SourceFile
from apps.api.core.queue import get_queue
from apps.api.services.ingestion import recover_stale_ingestions, touch_heartbeat

SHA_OLD = "d" * 40
_github_ids = iter(range(300_000, 400_000))
RUNNING_STALE = get_settings().ingestion_stale_running_seconds
QUEUED_STALE = get_settings().ingestion_stale_queued_seconds


def _ago(seconds: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


async def _repo(db: AsyncSession, status: str, heartbeat: datetime | None, **fields: object) -> int:
    repo = Repository(
        github_repo_id=next(_github_ids),
        full_name="octo/demo",
        default_branch="main",
        ingestion_status=status,
        ingestion_heartbeat_at=heartbeat,
        **fields,
    )
    db.add(repo)
    await db.commit()
    return repo.id


async def _state(db: AsyncSession, repository_id: int) -> Repository:
    return (
        await db.execute(select(Repository).where(Repository.id == repository_id).execution_options(populate_existing=True))
    ).scalar_one()


# --- heartbeat -----------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_touch_heartbeat_refreshes_only_in_progress_rows(real_db_session: AsyncSession) -> None:
    running = await _repo(real_db_session, "in_progress", _ago(60))
    done = await _repo(real_db_session, "completed", None)
    queued = await _repo(real_db_session, "queued", _ago(60))

    assert await touch_heartbeat(real_db_session, running) is True
    assert await touch_heartbeat(real_db_session, done) is False
    assert await touch_heartbeat(real_db_session, queued) is False

    assert (datetime.now(timezone.utc) - (await _state(real_db_session, running)).ingestion_heartbeat_at).total_seconds() < 10
    assert (await _state(real_db_session, done)).ingestion_heartbeat_at is None  # never revived
    assert (await _state(real_db_session, queued)).ingestion_heartbeat_at < _ago(30)


def _ending_after(real_db_session: AsyncSession, good_ticks: int, first_error: Exception | None = None):  # type: ignore[no-untyped-def]
    """A session factory: optional first-call failure, `good_ticks` real sessions, then cancellation.

    Ending the loop from inside the factory (instead of cancelling the task mid-query) keeps the shared
    test session in a clean state; production cancels the loop while it is sleeping or on its own session.
    """
    calls = {"n": 0}

    @asynccontextmanager
    async def sessions():  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if first_error is not None and calls["n"] == 1:
            raise first_error
        if calls["n"] > good_ticks + (1 if first_error else 0):
            raise asyncio.CancelledError
        yield real_db_session

    return sessions, calls


@pytest.mark.anyio
async def test_heartbeat_loop_advances_the_heartbeat_until_cancelled(real_db_session: AsyncSession) -> None:
    repo_id = await _repo(real_db_session, "in_progress", _ago(600))
    before = (await _state(real_db_session, repo_id)).ingestion_heartbeat_at
    sessions, calls = _ending_after(real_db_session, good_ticks=3)

    with pytest.raises(asyncio.CancelledError):
        await heartbeat_loop(sessions, repo_id, 0.01)

    assert calls["n"] == 4  # three ticks, then the cancellation
    assert (await _state(real_db_session, repo_id)).ingestion_heartbeat_at > before + timedelta(seconds=500)


@pytest.mark.anyio
async def test_heartbeat_loop_survives_a_failed_tick(real_db_session: AsyncSession) -> None:
    repo_id = await _repo(real_db_session, "in_progress", _ago(600))
    sessions, calls = _ending_after(real_db_session, good_ticks=2, first_error=RuntimeError("database hiccup"))

    with pytest.raises(asyncio.CancelledError):
        await heartbeat_loop(sessions, repo_id, 0.01)

    assert calls["n"] == 4  # one failed tick, two good ones, then the cancellation
    assert (await _state(real_db_session, repo_id)).ingestion_heartbeat_at > _ago(60)


# --- stale detection and recovery ----------------------------------------------------------------


@pytest.mark.anyio
async def test_stale_running_and_queued_rows_are_recovered_and_fresh_ones_left_alone(
    real_db_session: AsyncSession,
) -> None:
    stale_running = await _repo(real_db_session, "in_progress", _ago(RUNNING_STALE + 30))
    never_beat = await _repo(real_db_session, "in_progress", None)
    stale_queued = await _repo(real_db_session, "queued", _ago(QUEUED_STALE + 30))
    fresh_running = await _repo(real_db_session, "in_progress", _ago(RUNNING_STALE - 60))
    fresh_queued = await _repo(real_db_session, "queued", _ago(QUEUED_STALE - 60))
    # A queued row older than the *running* threshold is normal: it is just waiting for a worker.
    waiting_queued = await _repo(real_db_session, "queued", _ago(RUNNING_STALE + 30))
    inactive = [await _repo(real_db_session, s, _ago(99_999)) for s in ("pending", "completed", "failed")]

    recovered = await recover_stale_ingestions(real_db_session, live_repository_ids=set())

    assert set(recovered) == {stale_running, never_beat, stale_queued}
    for repo_id in (stale_running, never_beat, stale_queued):
        state = await _state(real_db_session, repo_id)
        assert state.ingestion_status == "failed"
        assert state.ingestion_error == "Ingestion was interrupted; please try again"
        assert state.ingestion_heartbeat_at is None
    assert (await _state(real_db_session, fresh_running)).ingestion_status == "in_progress"
    assert (await _state(real_db_session, fresh_queued)).ingestion_status == "queued"
    assert (await _state(real_db_session, waiting_queued)).ingestion_status == "queued"
    assert [(await _state(real_db_session, r)).ingestion_status for r in inactive] == ["pending", "completed", "failed"]


@pytest.mark.anyio
async def test_a_job_rq_still_knows_about_is_never_recovered(real_db_session: AsyncSession) -> None:
    alive = await _repo(real_db_session, "in_progress", _ago(RUNNING_STALE + 300))
    dead = await _repo(real_db_session, "in_progress", _ago(RUNNING_STALE + 300))

    recovered = await recover_stale_ingestions(real_db_session, live_repository_ids={alive})

    assert recovered == [dead]
    assert (await _state(real_db_session, alive)).ingestion_status == "in_progress"


@pytest.mark.anyio
async def test_recovery_can_be_scoped_to_one_repository(real_db_session: AsyncSession) -> None:
    first = await _repo(real_db_session, "in_progress", _ago(RUNNING_STALE + 30))
    second = await _repo(real_db_session, "in_progress", _ago(RUNNING_STALE + 30))

    assert await recover_stale_ingestions(real_db_session, set(), repository_id=first) == [first]
    assert (await _state(real_db_session, second)).ingestion_status == "in_progress"


@pytest.mark.anyio
async def test_recovery_preserves_the_previous_snapshot(real_db_session: AsyncSession) -> None:
    repo_id = await _repo(
        real_db_session,
        "in_progress",
        _ago(RUNNING_STALE + 30),
        last_ingested_commit_sha=SHA_OLD,
        last_ingested_at=_ago(3600),
    )
    real_db_session.add(
        SourceFile(
            repository_id=repo_id, commit_sha=SHA_OLD, path="old.py", language="python", category="source",
            content="old\n", content_hash="a" * 64, size_bytes=4, line_count=1,
        )
    )
    await real_db_session.commit()

    await recover_stale_ingestions(real_db_session, set())

    state = await _state(real_db_session, repo_id)
    assert state.ingestion_status == "failed"
    assert state.last_ingested_commit_sha == SHA_OLD
    assert state.last_ingested_at is not None
    paths = (await real_db_session.execute(select(SourceFile.path).where(SourceFile.repository_id == repo_id))).scalars().all()
    assert paths == ["old.py"]


@pytest.mark.anyio
async def test_sweep_skips_recovery_when_rq_state_is_unreadable(
    real_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_id = await _repo(real_db_session, "in_progress", _ago(RUNNING_STALE + 300))

    def broken(_queue: object) -> set[int]:
        raise RedisError("redis down")

    monkeypatch.setattr("apps.api.jobs.maintenance.live_repository_ids", broken)

    assert await sweep_stale_ingestions(real_db_session, MagicMock()) == []
    assert (await _state(real_db_session, repo_id)).ingestion_status == "in_progress"  # not guessed


@pytest.mark.anyio
async def test_sweep_recovers_when_rq_has_no_live_job(
    real_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_id = await _repo(real_db_session, "queued", _ago(QUEUED_STALE + 60))
    monkeypatch.setattr("apps.api.jobs.maintenance.live_repository_ids", lambda _queue: set())

    assert await sweep_stale_ingestions(real_db_session, MagicMock()) == [repo_id]


# --- POST recovers a stuck repository ------------------------------------------------------------


@pytest.fixture
async def post_client(real_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    queue = MagicMock()
    app.dependency_overrides[get_queue] = lambda: queue
    live: set[int] = set()
    monkeypatch.setattr("apps.api.jobs.maintenance.live_repository_ids", lambda _queue: live)
    try:
        yield real_client, queue, live
    finally:
        app.dependency_overrides.pop(get_queue, None)


@pytest.mark.anyio
async def test_post_recovers_a_stale_repository_and_requeues(post_client, real_db_session: AsyncSession) -> None:  # type: ignore[no-untyped-def]
    client, queue, _live = post_client
    repo_id = await _repo(real_db_session, "in_progress", _ago(RUNNING_STALE + 300))

    response = await client.post(f"/repositories/{repo_id}/ingestions")

    assert response.status_code == 202
    queue.enqueue.assert_called_once()
    assert (await _state(real_db_session, repo_id)).ingestion_status == "queued"


@pytest.mark.anyio
async def test_post_returns_409_when_stale_row_still_has_a_live_rq_job(post_client, real_db_session: AsyncSession) -> None:  # type: ignore[no-untyped-def]
    client, queue, live = post_client
    repo_id = await _repo(real_db_session, "in_progress", _ago(RUNNING_STALE + 300))
    live.add(repo_id)

    response = await client.post(f"/repositories/{repo_id}/ingestions")

    assert response.status_code == 409
    queue.enqueue.assert_not_called()
    assert (await _state(real_db_session, repo_id)).ingestion_status == "in_progress"


# --- RQ liveness ---------------------------------------------------------------------------------


@pytest.fixture
def test_queue() -> Queue:
    connection = Redis.from_url(get_settings().redis_url, db=15)
    try:
        connection.ping()
    except RedisError:
        pytest.skip("Redis is not available")
    queue = Queue(f"repolens-test-{uuid.uuid4().hex}", connection=connection)
    yield queue
    queue.empty()
    queue.delete()
    connection.close()


def test_live_repository_ids_sees_queued_ingestion_jobs_only(test_queue: Queue) -> None:
    assert live_repository_ids(test_queue) == set()
    job = enqueue_ingestion(test_queue, 41)
    other = test_queue.enqueue("builtins.len", [1, 2])  # not an ingestion job: ignored
    try:
        assert live_repository_ids(test_queue) == {41}
    finally:
        job.delete()
        other.delete()


def test_a_running_job_only_counts_while_its_worker_heartbeat_is_fresh(
    test_queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = test_queue.connection
    now = datetime.now(timezone.utc)
    workers = {
        "rq:worker:alive": SimpleNamespace(last_heartbeat=now - timedelta(seconds=5)),
        "rq:worker:silent": SimpleNamespace(last_heartbeat=now - timedelta(seconds=300)),
        "rq:worker:never": SimpleNamespace(last_heartbeat=None),
    }
    monkeypatch.setattr(
        "apps.api.core.queue.Worker.find_by_key", lambda key, connection=None: workers.get(key)
    )

    assert _worker_is_alive("alive", connection, max_age_seconds=60) is True
    assert _worker_is_alive("silent", connection, max_age_seconds=60) is False  # crashed: heartbeat stopped
    assert _worker_is_alive("never", connection, max_age_seconds=60) is False
    assert _worker_is_alive("gone", connection, max_age_seconds=60) is False  # key expired
    assert _worker_is_alive(None, connection, max_age_seconds=60) is False


def test_started_job_of_a_dead_worker_does_not_protect_its_repository(
    test_queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = enqueue_ingestion(test_queue, 52)
    # RQ records the running worker with a direct hash write when a job starts.
    test_queue.connection.hset(job.key, "worker_name", "crashed")
    try:
        # Pretend RQ lists it as started on a worker whose heartbeat stopped.
        monkeypatch.setattr(
            "apps.api.core.queue.StartedJobRegistry.get_job_ids", lambda self, *a, **k: [job.id]
        )
        monkeypatch.setattr("apps.api.core.queue.Worker.find_by_key", lambda key, connection=None: None)
        test_queue.connection.lrem(test_queue.key, 0, job.id)  # no longer waiting in the queue either

        assert live_repository_ids(test_queue) == set()

        alive = SimpleNamespace(last_heartbeat=datetime.now(timezone.utc))
        monkeypatch.setattr("apps.api.core.queue.Worker.find_by_key", lambda key, connection=None: alive)
        assert live_repository_ids(test_queue) == {52}
    finally:
        job.delete()


# --- orphaned temp archives ----------------------------------------------------------------------


def test_orphan_cleanup_removes_only_old_files_with_our_prefix(tmp_path: Path) -> None:
    old = tmp_path / f"{ARCHIVE_PREFIX}old.zip"
    fresh = tmp_path / f"{ARCHIVE_PREFIX}fresh.zip"
    unrelated = tmp_path / "other-project-old.zip"
    wrong_ext = tmp_path / f"{ARCHIVE_PREFIX}old.txt"
    directory = tmp_path / f"{ARCHIVE_PREFIX}dir.zip"
    directory.mkdir()
    for path in (old, fresh, unrelated, wrong_ext):
        path.write_bytes(b"x")
    long_ago = time.time() - 7200
    for path in (old, unrelated, wrong_ext, directory):
        os.utime(path, (long_ago, long_ago))

    removed = cleanup_orphan_archives(max_age_seconds=1800, directory=tmp_path)

    assert removed == [old]
    assert not old.exists()
    assert fresh.exists() and unrelated.exists() and wrong_ext.exists() and directory.exists()


def test_orphan_prefix_matches_what_the_downloader_creates() -> None:
    import inspect

    from apps.api.services import github_archive

    assert f'prefix="{ARCHIVE_PREFIX}"' in inspect.getsource(github_archive.GitHubArchiveService._stream_to_tempfile)
