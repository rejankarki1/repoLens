"""Architecture analysis end to end against the test database: queueing, running, reading, staleness and isolation."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

import apps.api.jobs.architecture_job as architecture_job
import apps.api.jobs.ingestion_job as ingestion_job
from apps.api.config import get_settings
from apps.api.core.queue import ARCHITECTURE_JOB_FUNC, enqueue_architecture, get_queue
from apps.api.main import app
from apps.api.models.entities import ArchitectureAnalysis, Repository, SourceFile
from apps.api.services.architecture import store
from tests.search_support import SHA_A, SHA_B, ingest, new_repository

pytestmark = pytest.mark.anyio

WEB = {
    "package.json": ('{"name": "web", "dependencies": {"next": "16"}}', "json"),
    "tsconfig.json": ('{"compilerOptions": {"paths": {"@/*": ["./*"]}}}', "json"),
    "app/page.tsx": ('import { api } from "@/lib/api"\nexport default function P() { return api }\n', "tsx"),
    "lib/api.ts": ('import { get } from "./http"\nexport const api = get\n', "typescript"),
    "lib/http.ts": ("export const get = 1\n", "typescript"),
    "svc/__init__.py": ("", "python"),
    "svc/a.py": ("from svc import b\n", "python"),
    "svc/b.py": ("import os\n", "python"),
}
V2 = {**WEB, "svc/c.py": ("from svc import a\n", "python")}


@asynccontextmanager
async def _same_session(db: AsyncSession) -> AsyncIterator[AsyncSession]:
    yield db


@pytest.fixture
async def env(real_client: httpx.AsyncClient, real_db_session: AsyncSession) -> AsyncIterator[dict]:
    state = {"client": real_client, "db": real_db_session, "queue": MagicMock(), "sessions": lambda: _same_session(real_db_session)}
    app.dependency_overrides[get_queue] = lambda: state["queue"]
    try:
        yield state
    finally:
        app.dependency_overrides.pop(get_queue, None)


async def repo_with(env: dict, files=WEB, sha=SHA_A, name="octo/arch") -> int:
    repo_id = await new_repository(env["db"], name)
    await ingest(env["db"], repo_id, files, sha=sha)
    return repo_id


async def run(env: dict, repo_id: int) -> str:
    return await store.run_analysis(env["sessions"], repo_id)


async def get(env: dict, repo_id: int, path: str = "", **params) -> httpx.Response:
    return await env["client"].get(f"/repositories/{repo_id}/architecture{path}", params=params)


async def post(env: dict, repo_id: int, **params) -> httpx.Response:
    return await env["client"].post(f"/repositories/{repo_id}/architecture", params=params)


# --- requesting and reading ---------------------------------------------------------------------------------------


async def test_unknown_repository_is_404_everywhere(env: dict) -> None:
    for response in (await post(env, 999999), await get(env, 999999), await get(env, 999999, "/status"), await get(env, 999999, "/nodes", id="module:x")):
        assert response.status_code == 404


async def test_repository_without_an_ingested_commit_is_409(env: dict) -> None:
    repo_id = await new_repository(env["db"])
    assert (await post(env, repo_id)).status_code == 409
    env["queue"].enqueue.assert_not_called()


async def test_before_any_analysis_status_is_none_and_the_graph_is_404(env: dict) -> None:
    repo_id = await repo_with(env)
    status = (await get(env, repo_id, "/status")).json()
    assert (status["status"], status["freshness"], status["has_result"], status["current_commit_sha"]) == ("none", "none", False, SHA_A)
    assert (await get(env, repo_id)).status_code == 404 and (await get(env, repo_id, "/nodes", id="module:app")).status_code == 404


async def test_requesting_queues_one_job_and_repeating_does_not_queue_another(env: dict) -> None:
    repo_id = await repo_with(env)
    first = await post(env, repo_id)
    assert first.status_code == 202 and first.json()["enqueued"] is True and first.json()["analysis"]["status"] == "queued"
    again = await post(env, repo_id)
    assert again.status_code == 202 and again.json()["enqueued"] is False
    assert env["queue"].enqueue.call_count == 1
    assert env["queue"].enqueue.call_args.args[1] == repo_id


async def test_running_then_reading_the_graph(env: dict) -> None:
    repo_id = await repo_with(env)
    await post(env, repo_id)
    assert await run(env, repo_id) == "completed"
    status = (await get(env, repo_id, "/status")).json()
    assert (status["status"], status["freshness"], status["analyzed_commit_sha"]) == ("completed", "current", SHA_A)

    body = (await get(env, repo_id)).json()
    assert body["analysis"]["freshness"] == "current" and body["graph"]["commit_sha"] == SHA_A
    graph = body["graph"]
    assert {n["id"] for n in graph["nodes"]} == {"module:app", "module:lib", "module:svc"}
    assert graph["stats"]["code_files"] == 6 and graph["stats"]["file_edges"] == 3
    [edge] = graph["edges"]
    assert (edge["source"], edge["target"], edge["basis"]) == ("module:app", "module:lib", "aggregated")
    assert edge["evidence"][0]["path"] == "app/page.tsx" and edge["evidence"][0]["start_line"] == 1

    assert (await post(env, repo_id)).status_code == 200  # already current: nothing to do
    assert (await post(env, repo_id)).json()["enqueued"] is False and env["queue"].enqueue.call_count == 1
    assert (await post(env, repo_id, force="true")).status_code == 202 and env["queue"].enqueue.call_count == 2


async def test_module_and_file_detail(env: dict) -> None:
    repo_id = await repo_with(env)
    await run(env, repo_id)
    module = (await get(env, repo_id, "/nodes", id="module:lib")).json()
    assert module["node"]["type"] == "module" and {m["path"] for m in module["members"]} == {"lib/api.ts", "lib/http.ts"}
    assert [e["id"] for e in module["internal_edges"]] == ["edge:file:lib/api.ts->lib/http.ts:imports"]
    assert [(e["source"], e["target"]) for e in module["incoming"]] == [("file:app/page.tsx", "file:lib/api.ts")]
    assert module["outgoing"] == [] and [n["path"] for n in module["related_nodes"]] == ["app/page.tsx"]

    file = (await get(env, repo_id, "/nodes", id="file:app/page.tsx")).json()
    assert file["node"]["kind"] == "page" and file["members"] == [] and file["internal_edges"] == []
    assert [e["target"] for e in file["outgoing"]] == ["file:lib/api.ts"] and file["incoming"] == []
    other = (await get(env, repo_id, "/nodes", id="module:svc")).json()
    assert {x["name"] for x in other["external_dependencies"]} == {"os"}


async def test_unknown_or_malformed_node_ids_are_404(env: dict) -> None:
    repo_id = await repo_with(env)
    await run(env, repo_id)
    for node_id in ("module:nope", "file:nope.py", "bogus", "module:"):
        assert (await get(env, repo_id, "/nodes", id=node_id)).status_code == 404
    assert (await get(env, repo_id, "/nodes", id="")).status_code == 422


# --- freshness, failures, recovery --------------------------------------------------------------------------------------


async def test_reingestion_makes_the_result_stale_not_wrong(env: dict) -> None:
    repo_id = await repo_with(env)
    await run(env, repo_id)
    await ingest(env["db"], repo_id, V2, sha=SHA_B)

    status = (await get(env, repo_id, "/status")).json()
    assert (status["freshness"], status["analyzed_commit_sha"], status["current_commit_sha"], status["has_result"]) == ("stale", SHA_A, SHA_B, True)
    old = (await get(env, repo_id)).json()
    assert old["graph"]["commit_sha"] == SHA_A and old["analysis"]["freshness"] == "stale" and old["graph"]["stats"]["code_files"] == 6

    assert (await post(env, repo_id)).status_code == 202  # a stale result is replaced on request
    assert await run(env, repo_id) == "completed"
    fresh = (await get(env, repo_id)).json()
    assert fresh["analysis"]["freshness"] == "current" and fresh["graph"]["commit_sha"] == SHA_B and fresh["graph"]["stats"]["code_files"] == 7


async def test_failed_reanalysis_preserves_the_last_completed_result(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_id = await repo_with(env)
    await run(env, repo_id)
    await ingest(env["db"], repo_id, V2, sha=SHA_B)
    await post(env, repo_id)

    def explode(*args, **kwargs):
        raise RuntimeError("boom with a secret path /home/x")

    monkeypatch.setattr(store, "analyze_snapshot", explode)
    assert await run(env, repo_id) == "failed"
    status = (await get(env, repo_id, "/status")).json()
    assert status["status"] == "failed" and status["freshness"] == "stale" and status["analyzed_commit_sha"] == SHA_A
    assert "RuntimeError" in status["error"] and "secret" not in status["error"]
    kept = (await get(env, repo_id)).json()
    assert kept["graph"]["commit_sha"] == SHA_A and kept["graph"]["stats"]["code_files"] == 6

    monkeypatch.undo()
    assert (await post(env, repo_id)).status_code == 202  # a failed attempt can be retried
    assert await run(env, repo_id) == "completed" and (await get(env, repo_id, "/status")).json()["freshness"] == "current"


async def test_lost_jobs_are_reported_and_can_be_restarted(env: dict) -> None:
    repo_id = await repo_with(env)
    await post(env, repo_id)
    long_ago = datetime.now(timezone.utc) - timedelta(seconds=get_settings().architecture_stale_seconds + 5)
    await env["db"].execute(update(ArchitectureAnalysis).values(updated_at=long_ago))
    await env["db"].commit()
    status = (await get(env, repo_id, "/status")).json()
    assert status["status"] == "failed" and "stopped unexpectedly" in status["error"]
    again = await post(env, repo_id)
    assert again.json()["enqueued"] is True and again.json()["analysis"]["status"] == "queued" and env["queue"].enqueue.call_count == 2


async def test_enqueue_failure_is_503_and_recorded(env: dict) -> None:
    repo_id = await repo_with(env)
    env["queue"].enqueue.side_effect = ConnectionError("redis down")
    assert (await post(env, repo_id)).status_code == 503
    status = (await get(env, repo_id, "/status")).json()
    assert status["status"] == "failed" and "Could not queue" in status["error"]
    env["queue"].enqueue.side_effect = None
    assert (await post(env, repo_id)).status_code == 202


async def test_a_running_analysis_is_not_started_twice(env: dict) -> None:
    repo_id = await repo_with(env)
    await env["db"].execute(ArchitectureAnalysis.__table__.insert().values(repository_id=repo_id, status="running", target_commit_sha=SHA_A))
    await env["db"].commit()
    assert await run(env, repo_id) == "skipped"
    assert (await post(env, repo_id)).json()["enqueued"] is False


async def test_a_result_for_a_superseded_target_is_discarded(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_id = await repo_with(env)
    real_load = store.load_snapshot

    async def load_then_get_superseded(db, repository_id, commit_sha):
        files = await real_load(db, repository_id, commit_sha)
        await db.execute(update(ArchitectureAnalysis).values(target_commit_sha=SHA_B, status="queued"))  # a newer request took the row
        await db.commit()
        return files

    monkeypatch.setattr(store, "load_snapshot", load_then_get_superseded)
    assert await run(env, repo_id) == "skipped"
    row = (await env["db"].execute(select(ArchitectureAnalysis))).scalar_one()
    assert (row.status, row.target_commit_sha, row.result_commit_sha, row.summary) == ("queued", SHA_B, None, None)


async def test_snapshot_changed_during_analysis_fails_instead_of_mixing_commits(env: dict) -> None:
    repo_id = await repo_with(env)

    class DeletesARowAfterTheFileListIsRead:
        def __init__(self, db):
            self._db, self._calls = db, 0

        async def execute(self, statement):
            result = await self._db.execute(statement)
            self._calls += 1
            if self._calls == 1:
                await self._db.execute(delete(SourceFile).where(SourceFile.path == "svc/b.py"))
            return result

    with pytest.raises(store.SnapshotChangedError):
        await store.load_snapshot(DeletesARowAfterTheFileListIsRead(env["db"]), repo_id, SHA_A)


# --- isolation -----------------------------------------------------------------------------------------------------------


async def test_only_the_current_commits_files_are_analyzed(env: dict) -> None:
    repo_id = await repo_with(env)
    await env["db"].execute(SourceFile.__table__.insert().values(
        repository_id=repo_id, commit_sha=SHA_B, path="other_commit.py", language="python", category="source", content="import svc\n",
        content_hash="0" * 64, size_bytes=11, line_count=1,
    ))
    await env["db"].commit()
    await run(env, repo_id)
    nodes = {n.path for n in (await store.get_node_detail(env["db"], repo_id, "module:svc")).members}
    assert nodes == {"svc/__init__.py", "svc/a.py", "svc/b.py"}
    assert (await get(env, repo_id)).json()["graph"]["stats"]["files_total"] == len(WEB)


async def test_repositories_do_not_see_each_others_graphs(env: dict) -> None:
    first = await repo_with(env, WEB, name="octo/first")
    second = await repo_with(env, {"only/x.py": ("import os\n", "python")}, name="octo/second")
    await run(env, first)
    await run(env, second)
    assert {n["id"] for n in (await get(env, second)).json()["graph"]["nodes"]} == {"module:only"}
    assert (await get(env, second, "/nodes", id="module:svc")).status_code == 404
    assert (await get(env, first, "/nodes", id="module:only")).status_code == 404


async def test_empty_and_unsupported_repositories_complete_with_a_warning(env: dict) -> None:
    repo_id = await repo_with(env, {"README.md": ("# hi\n", "markdown"), "data.json": ("{}", "json")}, name="octo/docs")
    assert await run(env, repo_id) == "completed"
    graph = (await get(env, repo_id)).json()["graph"]
    assert graph["nodes"] == [] and graph["stats"]["code_files"] == 0 and any("No supported source files" in w for w in graph["warnings"])


# --- jobs and hooks ----------------------------------------------------------------------------------------------------------


def test_enqueue_architecture_uses_the_shared_queue_without_retries() -> None:
    queue = MagicMock()
    enqueue_architecture(queue, 7)
    function, repository_id = queue.enqueue.call_args.args
    assert f"{function.__module__}.{function.__name__}" == ARCHITECTURE_JOB_FUNC and repository_id == 7
    assert queue.enqueue.call_args.kwargs["job_timeout"] == get_settings().ingestion_job_timeout_seconds
    assert "retry" not in queue.enqueue.call_args.kwargs


async def test_the_job_runs_the_analysis_and_disposes_its_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def fake_run(sessions, repository_id, settings):
        calls.append(repository_id)

    monkeypatch.setattr(architecture_job, "run_analysis", fake_run)
    await architecture_job._run(42)
    assert calls == [42]


@pytest.mark.parametrize(("completed", "expected"), [(True, [5]), (False, []), (None, [])])
def test_ingestion_queues_architecture_only_after_a_completed_ingestion(monkeypatch: pytest.MonkeyPatch, completed, expected) -> None:
    queued = []

    async def fake_run(repository_id, will_retry=False):
        return completed

    monkeypatch.setattr(ingestion_job, "_run", fake_run)
    monkeypatch.setattr(ingestion_job, "_queue_architecture", queued.append)
    monkeypatch.setattr(ingestion_job, "get_current_job", lambda: None)
    ingestion_job.ingest_repository_job(5)
    assert queued == expected


def test_failing_to_queue_architecture_never_breaks_ingestion(monkeypatch: pytest.MonkeyPatch) -> None:
    async def explode(repository_id):
        raise ConnectionError("redis down")

    monkeypatch.setattr(ingestion_job, "_queue_architecture_async", explode)
    ingestion_job._queue_architecture(5)  # logs a warning, raises nothing


async def test_node_detail_reports_the_cycle_a_node_belongs_to(env: dict) -> None:
    files = {"c/a.ts": ("import { b } from './b'\nexport const a = 1\n", "typescript"), "c/b.ts": ("import { a } from './a'\nexport const b = 1\n", "typescript"),
             "c/c.ts": ("export const c = 1\n", "typescript")}
    repo_id = await repo_with(env, files, name="octo/cyclic")
    await run(env, repo_id)
    detail = (await get(env, repo_id, "/nodes", id="file:c/a.ts")).json()
    assert [c["nodes"] for c in detail["cycles"]] == [["file:c/a.ts", "file:c/b.ts"]] and detail["node"]["cycle_id"] == detail["cycles"][0]["id"]
    assert (await get(env, repo_id, "/nodes", id="file:c/c.ts")).json()["cycles"] == []
    module = (await get(env, repo_id, "/nodes", id="module:c")).json()
    assert len(module["cycles"]) == 1 and all(e["cycle_id"] for e in module["internal_edges"])
