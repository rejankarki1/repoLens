"""HTTP tests for the search endpoints and for how embedding jobs get started."""

import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from redis import Redis
from redis.exceptions import RedisError
from rq import Queue
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

import apps.api.jobs.ingestion_job as ingestion_job
from apps.api.config import Settings, get_settings
from apps.api.core.queue import enqueue_embedding, get_queue
from apps.api.main import app
from apps.api.models.entities import Repository
from apps.api.services.embeddings import embedding_lock, get_embedder
from apps.api.services.source_file_persistence import persist_source_snapshot
from tests.search_support import CORPUS, SHA_A, FakeEmbedder, ingest, new_repository, snapshot

pytestmark = pytest.mark.anyio


@pytest.fixture
async def api(real_client: httpx.AsyncClient) -> AsyncIterator[dict[str, object]]:
    """The API with a fake embedder and a mock RQ queue; tests may swap either one."""
    state: dict[str, object] = {"embedder": FakeEmbedder(), "queue": MagicMock(), "client": real_client}
    app.dependency_overrides[get_embedder] = lambda: state["embedder"]
    app.dependency_overrides[get_queue] = lambda: state["queue"]
    try:
        yield state
    finally:
        app.dependency_overrides.pop(get_embedder, None)
        app.dependency_overrides.pop(get_queue, None)


@pytest.fixture
async def engine(_prepared_test_database: None) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(make_url(get_settings().database_url).set(database="repolens_test"))
    yield engine
    await engine.dispose()


async def _get(api: dict[str, object], url: str, **params: object) -> httpx.Response:
    return await api["client"].get(url, params=params)  # type: ignore[attr-defined]


# --- GET /repositories/{id}/search ------------------------------------------------------------------------------


async def test_search_returns_cited_hits_with_index_status(api: dict[str, object], real_db_session: AsyncSession) -> None:
    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id)

    response = await _get(api, f"/repositories/{repo_id}/search", q="loadEnvLocal", mode="keyword")

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "loadEnvLocal" and body["mode"] == "keyword" and body["warnings"] == [] and body["took_ms"] >= 0
    top = body["hits"][0]
    assert top["path"] == "scripts/env.ts" and top["symbol_name"] == "loadEnvLocal" and top["kind"] == "function"
    assert set(top) == {
        "chunk_id", "repository_id", "commit_sha", "source_file_id", "path", "language", "kind", "symbol_name", "symbols",
        "parser_mode", "start_line", "end_line", "start_byte", "end_byte", "content", "score", "matched_by",
        "keyword_rank", "semantic_rank", "exact_rank",
    }
    data = CORPUS["scripts/env.ts"][0].encode()
    assert data[top["start_byte"] : top["end_byte"]].decode() == top["content"]  # the citation is exact
    assert (top["repository_id"], top["commit_sha"], top["matched_by"]) == (repo_id, SHA_A, ["keyword", "exact"])
    assert body["index"]["chunks"] > 0 and body["index"]["semantic"] == "not_built"


async def test_hybrid_search_over_http_uses_both_retrievers(api: dict[str, object], real_db_session: AsyncSession, engine: AsyncEngine) -> None:
    from apps.api.services.embeddings import build_embeddings
    from tests.test_code_search import _sessions

    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id)
    await build_embeddings(engine, _sessions(real_db_session), repo_id, api["embedder"], Settings())  # type: ignore[arg-type]

    body = (await _get(api, f"/repositories/{repo_id}/search", q="verify password stored hash", limit=3)).json()

    assert body["mode"] == "hybrid" and body["index"]["semantic"] == "ready" and len(body["hits"]) == 3
    assert body["hits"][0]["symbol_name"] == "verify_password" and body["hits"][0]["matched_by"] == ["keyword", "semantic"]


@pytest.mark.parametrize(
    "params",
    [{}, {"q": ""}, {"q": "   "}, {"q": "x" * 501}, {"q": "ok", "limit": 0}, {"q": "ok", "limit": 51}, {"q": "ok", "mode": "fuzzy"}],
)
async def test_invalid_search_requests_are_rejected(api: dict[str, object], real_db_session: AsyncSession, params: dict[str, object]) -> None:
    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id)

    assert (await _get(api, f"/repositories/{repo_id}/search", **params)).status_code == 422


async def test_unknown_unindexed_and_chunkless_repositories(api: dict[str, object], real_db_session: AsyncSession) -> None:
    db = real_db_session
    assert (await _get(api, "/repositories/999999999/search", q="x")).status_code == 404
    fresh = Repository(github_repo_id=88_888_888, full_name="x/y", default_branch="main")
    db.add(fresh)
    await db.commit()
    never = await _get(api, f"/repositories/{fresh.id}/search", q="anything")
    assert never.status_code == 409 and "not been ingested" in never.json()["detail"]
    chunkless = await new_repository(db)
    await persist_source_snapshot(db, chunkless, snapshot(SHA_A, CORPUS))  # an older snapshot: files, no chunks
    legacy = await _get(api, f"/repositories/{chunkless}/search", q="anything")
    assert legacy.status_code == 409 and "re-analyze" in legacy.json()["detail"]


async def test_keyword_search_stays_usable_when_semantic_search_is_not(api: dict[str, object], real_db_session: AsyncSession) -> None:
    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id)

    api["embedder"] = None  # no API key configured
    hybrid = (await _get(api, f"/repositories/{repo_id}/search", q="verify_password")).json()
    assert hybrid["mode"] == "keyword" and hybrid["hits"][0]["symbol_name"] == "verify_password"
    assert hybrid["index"]["semantic"] == "disabled" and "not configured" in hybrid["warnings"][0]
    assert (await _get(api, f"/repositories/{repo_id}/search", q="verify", mode="semantic")).status_code == 409

    api["embedder"] = FakeEmbedder(fail_queries=True)  # the provider is down
    outage = await _get(api, f"/repositories/{repo_id}/search", q="verify_password")
    assert outage.status_code == 200  # (no vectors exist yet, so this is the "not built" fallback)
    assert outage.json()["mode"] == "keyword"


async def test_semantic_only_search_reports_a_provider_outage_as_503(api: dict[str, object], real_db_session: AsyncSession, engine: AsyncEngine) -> None:
    from apps.api.services.embeddings import build_embeddings
    from tests.test_code_search import _sessions

    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id)
    await build_embeddings(engine, _sessions(real_db_session), repo_id, FakeEmbedder(), Settings())  # type: ignore[arg-type]
    api["embedder"] = FakeEmbedder(fail_queries=True)

    semantic = await _get(api, f"/repositories/{repo_id}/search", q="password", mode="semantic")
    hybrid = await _get(api, f"/repositories/{repo_id}/search", q="password")

    assert semantic.status_code == 503 and "temporarily unavailable" in semantic.json()["detail"]
    assert hybrid.status_code == 200 and hybrid.json()["mode"] == "keyword" and hybrid.json()["warnings"]  # still usable


# --- GET /search-index and POST /embeddings --------------------------------------------------------------------------


async def test_search_index_endpoint_reports_readiness(api: dict[str, object], real_db_session: AsyncSession) -> None:
    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id)

    body = (await _get(api, f"/repositories/{repo_id}/search-index")).json()

    assert body["repository_id"] == repo_id and body["commit_sha"] == SHA_A and body["chunks"] > 0
    assert (body["embedded_chunks"], body["semantic"], body["embedding_job_running"]) == (0, "not_built", False)
    assert body["model"] == "gemini-embedding-001/1536"
    assert (await _get(api, "/repositories/999999999/search-index")).status_code == 404


async def test_embeddings_endpoint_queues_the_job(api: dict[str, object], real_db_session: AsyncSession) -> None:
    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id)

    response = await api["client"].post(f"/repositories/{repo_id}/embeddings")  # type: ignore[attr-defined]

    assert response.status_code == 202 and response.json()["semantic"] == "not_built"
    queue = api["queue"]
    queue.enqueue.assert_called_once()  # type: ignore[attr-defined]
    function, argument = queue.enqueue.call_args.args[:2]  # type: ignore[attr-defined]
    assert function.__name__ == "embed_repository_job" and argument == repo_id


async def test_embeddings_endpoint_refusals(api: dict[str, object], real_db_session: AsyncSession, engine: AsyncEngine) -> None:
    db, client = real_db_session, api["client"]
    post = lambda repo_id: client.post(f"/repositories/{repo_id}/embeddings")  # type: ignore[attr-defined]
    repo_id = await new_repository(db)
    chunkless = await new_repository(db)
    await persist_source_snapshot(db, chunkless, snapshot(SHA_A, CORPUS))
    await ingest(db, repo_id)

    assert (await post(999_999_999)).status_code == 404
    assert (await post(chunkless)).status_code == 409  # nothing to embed
    async with embedding_lock(engine, repo_id):
        running = await post(repo_id)
    assert running.status_code == 409 and "already running" in running.json()["detail"]
    api["queue"].enqueue.side_effect = RedisError("redis down")  # type: ignore[attr-defined]
    assert (await post(repo_id)).status_code == 503
    api["queue"].enqueue.side_effect = None  # type: ignore[attr-defined]
    api["embedder"] = None
    not_configured = await post(repo_id)
    assert not_configured.status_code == 409 and "GEMINI_API_KEY" in not_configured.json()["detail"]
    assert api["queue"].enqueue.call_count == 1  # type: ignore[attr-defined]  # only the failed attempt reached Redis


# --- starting embeddings after ingestion --------------------------------------------------------------------------------


def _hook(monkeypatch: pytest.MonkeyPatch, *, completed: object, key: str | None) -> tuple[MagicMock, MagicMock]:
    monkeypatch.setattr(ingestion_job, "_run", AsyncMock(return_value=completed))
    monkeypatch.setattr(ingestion_job, "get_settings", lambda: Settings(gemini_api_key=key))
    enqueue, queue = MagicMock(), MagicMock()
    monkeypatch.setattr("apps.api.core.queue.enqueue_embedding", enqueue)
    monkeypatch.setattr("apps.api.core.queue.get_ingestion_queue", lambda: queue)
    return enqueue, queue


def test_a_completed_ingestion_queues_embeddings_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    enqueue, queue = _hook(monkeypatch, completed=True, key="sk-test")

    ingestion_job.ingest_repository_job(7)

    enqueue.assert_called_once_with(queue, 7)


@pytest.mark.parametrize(("completed", "key"), [(False, "sk-test"), (True, None), (None, "sk-test")])
def test_no_embeddings_are_queued_for_failed_skipped_or_unconfigured_ingestions(
    monkeypatch: pytest.MonkeyPatch, completed: object, key: str | None
) -> None:
    enqueue, _ = _hook(monkeypatch, completed=completed, key=key)

    ingestion_job.ingest_repository_job(7)

    enqueue.assert_not_called()


def test_a_failure_to_queue_embeddings_never_fails_the_ingestion(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    enqueue, _ = _hook(monkeypatch, completed=True, key="sk-test")
    enqueue.side_effect = RedisError("redis down")

    ingestion_job.ingest_repository_job(7)  # returns normally: ingestion is already completed

    assert "Could not enqueue embeddings for repository 7" in caplog.text


async def test_run_ingestion_reports_whether_the_repository_completed(real_db_session: AsyncSession, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import io
    import zipfile

    from apps.api.jobs.ingestion_job import run_ingestion
    from apps.api.services.github_archive import ArchiveDownloadResult, MalformedArchiveError
    from apps.api.services.ingestion import request_ingestion

    db, archives = real_db_session, AsyncMock()
    repo_id = await new_repository(db)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("octo-demo-abc/app.py", "x = 1\n")
    zip_path = tmp_path / "a.zip"
    zip_path.write_bytes(buffer.getvalue())
    archives.fetch_repository_archive.return_value = ArchiveDownloadResult(archive_path=zip_path, commit_sha=SHA_A)

    await request_ingestion(db, repo_id)
    assert await run_ingestion(db, repo_id, archives) is True
    assert await run_ingestion(db, repo_id, archives) is False  # not queued any more: skipped
    await request_ingestion(db, repo_id)
    archives.fetch_repository_archive.side_effect = MalformedArchiveError("bad")
    assert await run_ingestion(db, repo_id, archives) is False  # permanent failure


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


def test_the_embedding_job_has_its_own_longer_retry_schedule(test_queue: Queue) -> None:
    job = enqueue_embedding(test_queue, 9, Settings())

    try:
        assert job.func_name == "apps.api.jobs.embedding_job.embed_repository_job" and job.args == (9,)
        # rate limits clear per minute (or per day): far longer than the 30 s / 2 min schedule used for ingestion
        assert (job.timeout, job.retries_left, job.retry_intervals) == (900, 4, [60, 180, 300, 600])
    finally:
        job.delete()


async def test_a_second_embedding_job_is_not_queued_while_one_is_pending(
    api: dict[str, object], real_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id)
    client, queue = api["client"], api["queue"]
    pending: set[int] = {repo_id}
    monkeypatch.setattr("apps.api.routes.search.live_repository_ids", lambda _q, _s=None, _f=None: pending)

    duplicate = await client.post(f"/repositories/{repo_id}/embeddings")  # type: ignore[attr-defined]
    assert duplicate.status_code == 409 and "already queued" in duplicate.json()["detail"]
    queue.enqueue.assert_not_called()  # type: ignore[attr-defined]

    pending.clear()  # the earlier job finished or failed for good: a new one may be queued
    assert (await client.post(f"/repositories/{repo_id}/embeddings")).status_code == 202  # type: ignore[attr-defined]
    queue.enqueue.assert_called_once()  # type: ignore[attr-defined]


def test_live_lookup_can_be_asked_about_embedding_jobs(test_queue: Queue) -> None:
    from apps.api.core.queue import EMBEDDING_JOB_FUNC, live_repository_ids

    job = enqueue_embedding(test_queue, 41, Settings())
    try:
        assert live_repository_ids(test_queue, None, EMBEDDING_JOB_FUNC) == {41}
        assert live_repository_ids(test_queue) == set()  # the default (ingestion) view ignores embedding jobs
    finally:
        job.delete()
