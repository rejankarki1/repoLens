"""Architecture Diagram V2 persistence end to end against the test database: queueing, running (with a
fake AI provider — no live call is ever made here), reading, staleness, retries and isolation. Mirrors
tests/test_architecture_api.py's structure for the deterministic graph one layer down."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

import apps.api.jobs.architecture_diagram_job as architecture_diagram_job
from apps.api.config import get_settings
from apps.api.core.queue import ARCHITECTURE_DIAGRAM_JOB_FUNC, enqueue_architecture_diagram, get_queue
from apps.api.main import app
from apps.api.models.entities import ArchitectureDiagramRecord
from apps.api.services.architecture import store as architecture_store
from apps.api.services.architecture_diagram import store
from apps.api.services.generation import GenerationError, GenerationResult
from tests.search_support import SHA_A, SHA_B, ingest, new_repository

pytestmark = pytest.mark.anyio

WEB = {
    "backend/routes/users.py": ("from fastapi import APIRouter\n\nrouter = APIRouter()\n\n\n@router.get('/users')\ndef list_users():\n    return []\n", "python"),
    "backend/models.py": ("from sqlalchemy.orm import DeclarativeBase\n\n\nclass Base(DeclarativeBase):\n    pass\n\n\nclass User(Base):\n    __tablename__ = 'users'\n", "python"),
    "pyproject.toml": ('[project]\nname = "demo"\ndependencies = ["fastapi", "sqlalchemy"]\n', "toml"),
}


@asynccontextmanager
async def _same_session(db: AsyncSession) -> AsyncIterator[AsyncSession]:
    yield db


class FakeGenerator:
    """Stands in for `GeminiGenerator`/`GroqGenerator`: no HTTP request of any kind."""

    provider = "fake"
    model = "fake-model"

    def __init__(self, text: str | None = None, *, error: GenerationError | None = None) -> None:
        self.text = text
        self.error = error
        self.calls = 0

    async def generate(self, system, contents, *, attempts: int = 2) -> GenerationResult:
        self.calls += 1
        if self.error:
            raise self.error
        return GenerationResult(self.text or "")

    def stream(self, system, contents):  # pragma: no cover - diagram generation never streams
        raise NotImplementedError


VALID_RESPONSE = json.dumps({
    "groups": [{"id": "g1", "label": "Backend", "description": "x"}],
    "components": [
        {"id": "c1", "group_id": "g1", "label": "Users API", "description": "x", "kind": "api", "source_paths": ["backend/routes/users.py"]},
        {"id": "c2", "group_id": "g1", "label": "Data Models", "description": "x", "kind": "database", "source_paths": ["backend/models.py"]},
    ],
    "relationships": [{"id": "r1", "source": "c1", "target": "c2", "label": "reads", "kind": "db_read"}],
})


@pytest.fixture
async def env(real_client: httpx.AsyncClient, real_db_session: AsyncSession) -> AsyncIterator[dict]:
    state = {"client": real_client, "db": real_db_session, "queue": MagicMock(), "sessions": lambda: _same_session(real_db_session)}
    app.dependency_overrides[get_queue] = lambda: state["queue"]
    try:
        yield state
    finally:
        app.dependency_overrides.pop(get_queue, None)


async def repo_with(env: dict, files=WEB, sha=SHA_A, name="octo/diag", *, analyze: bool = True) -> int:
    """A repository with a stored snapshot and (by default) a completed deterministic analysis — the
    prerequisite `queue_diagram` checks for."""
    repo_id = await new_repository(env["db"], name)
    await ingest(env["db"], repo_id, files, sha=sha)
    if analyze:
        assert await architecture_store.run_analysis(env["sessions"], repo_id) == "completed"
    return repo_id


async def run_diagram(env: dict, repo_id: int, generator) -> str:
    return await store.run_diagram(env["sessions"], repo_id, generator=generator)


async def get(env: dict, repo_id: int, path: str = "") -> httpx.Response:
    return await env["client"].get(f"/repositories/{repo_id}/architecture-diagram{path}")


async def post(env: dict, repo_id: int, **params) -> httpx.Response:
    return await env["client"].post(f"/repositories/{repo_id}/architecture-diagram", params=params)


# --- the prerequisite: a current deterministic analysis -------------------------------------------------------


async def test_queueing_without_a_deterministic_analysis_is_409(env: dict) -> None:
    repo_id = await repo_with(env, analyze=False)
    response = await post(env, repo_id)
    assert response.status_code == 409
    assert "architecture" in response.json()["detail"].lower()
    env["queue"].enqueue.assert_not_called()


async def test_queueing_with_a_stale_deterministic_analysis_is_409(env: dict) -> None:
    repo_id = await repo_with(env)
    await ingest(env["db"], repo_id, WEB, sha=SHA_B)  # re-ingested; the deterministic analysis now describes SHA_A, stale
    assert (await post(env, repo_id)).status_code == 409


# --- queueing: enqueue once, never repeat unnecessarily ---------------------------------------------------------


async def test_requesting_queues_one_job_and_repeating_does_not_queue_another(env: dict) -> None:
    repo_id = await repo_with(env)
    first = await post(env, repo_id)
    assert first.status_code == 202 and first.json()["enqueued"] is True and first.json()["status"]["status"] == "queued"
    again = await post(env, repo_id)
    assert again.status_code == 202 and again.json()["enqueued"] is False
    assert env["queue"].enqueue.call_count == 1  # the second request spent no AI budget


async def test_before_any_generation_status_is_none_and_the_diagram_is_404(env: dict) -> None:
    repo_id = await repo_with(env)
    status = (await get(env, repo_id, "/status")).json()
    assert (status["status"], status["freshness"], status["has_result"]) == ("none", "none", False)
    assert (await get(env, repo_id)).status_code == 404


# --- running (fake provider), reading -----------------------------------------------------------------------------


async def test_running_then_reading_the_diagram(env: dict) -> None:
    repo_id = await repo_with(env)
    await post(env, repo_id)
    generator = FakeGenerator(VALID_RESPONSE)
    assert await run_diagram(env, repo_id, generator) == "completed"
    assert generator.calls == 1

    status = (await get(env, repo_id, "/status")).json()
    assert (status["status"], status["freshness"], status["model"]) == ("completed", "current", "fake-model")

    body = (await get(env, repo_id)).json()
    assert body["status"]["freshness"] == "current"
    diagram = body["diagram"]
    assert {c["id"] for c in diagram["components"]} == {"c1", "c2"}
    assert diagram["relationships"][0]["confidence"] == "inferred"  # db_read is never verified from an import edge

    assert (await post(env, repo_id)).status_code == 200  # already current: nothing to do
    assert (await post(env, repo_id)).json()["enqueued"] is False and env["queue"].enqueue.call_count == 1
    assert (await post(env, repo_id, force="true")).status_code == 202 and env["queue"].enqueue.call_count == 2


async def test_a_fabricated_path_from_the_model_is_dropped_not_trusted(env: dict) -> None:
    repo_id = await repo_with(env)
    response = json.dumps({
        "groups": [], "relationships": [],
        "components": [{"id": "c1", "label": "Ghost", "description": "x", "kind": "service", "source_paths": ["made/up.py"]}],
    })
    assert await run_diagram(env, repo_id, FakeGenerator(response)) == "completed"
    diagram = (await get(env, repo_id)).json()["diagram"]
    assert diagram["components"][0]["source_paths"] == []
    assert diagram["components"][0]["evidence_basis"] == "inferred"


# --- failures: malformed response, provider errors, retry policy -------------------------------------------------


async def test_a_malformed_model_response_fails_cleanly_without_breaking_the_tab(env: dict) -> None:
    repo_id = await repo_with(env)
    assert await run_diagram(env, repo_id, FakeGenerator("not json at all")) == "failed"
    status = (await get(env, repo_id, "/status")).json()
    assert status["status"] == "failed" and "response" in status["error"].lower()
    assert (await get(env, repo_id)).status_code == 404  # no partial diagram was ever stored


async def test_a_permanent_provider_error_fails_and_is_not_retried(env: dict) -> None:
    repo_id = await repo_with(env)
    generator = FakeGenerator(error=GenerationError("auth", "bad key"))
    assert await run_diagram(env, repo_id, generator) == "failed"
    row = (await env["db"].execute(select(ArchitectureDiagramRecord))).scalar_one()
    assert row.status == "failed"


async def test_a_transient_provider_error_is_requeued_for_rq_to_retry(env: dict) -> None:
    repo_id = await repo_with(env)
    generator = FakeGenerator(error=GenerationError("rate_limited", "slow down"))
    with pytest.raises(GenerationError):
        await run_diagram(env, repo_id, generator)
    row = (await env["db"].execute(select(ArchitectureDiagramRecord))).scalar_one()
    assert row.status == "queued"  # not "running" and not "failed": a retry must be able to reclaim it
    assert "slow down" in row.error

    # the retry (a fresh run_diagram call, as RQ would make) can now succeed
    assert await run_diagram(env, repo_id, FakeGenerator(VALID_RESPONSE)) == "completed"
    assert (await get(env, repo_id, "/status")).json()["status"] == "completed"


async def test_lost_jobs_are_reported_and_can_be_restarted(env: dict) -> None:
    repo_id = await repo_with(env)
    await post(env, repo_id)
    long_ago = datetime.now(timezone.utc) - timedelta(seconds=get_settings().architecture_stale_seconds + 5)
    await env["db"].execute(update(ArchitectureDiagramRecord).values(updated_at=long_ago))
    await env["db"].commit()
    status = (await get(env, repo_id, "/status")).json()
    assert status["status"] == "failed" and "stopped unexpectedly" in status["error"]
    again = await post(env, repo_id)
    assert again.json()["enqueued"] is True and env["queue"].enqueue.call_count == 2


# --- staleness: re-ingestion and a changed deterministic analyzer version -----------------------------------------


async def test_reingestion_makes_the_result_stale_not_wrong(env: dict) -> None:
    repo_id = await repo_with(env)
    await run_diagram(env, repo_id, FakeGenerator(VALID_RESPONSE))
    await ingest(env["db"], repo_id, WEB, sha=SHA_B)
    await architecture_store.run_analysis(env["sessions"], repo_id)  # re-analyze deterministically at the new commit

    status = (await get(env, repo_id, "/status")).json()
    assert status["freshness"] == "stale" and status["has_result"] is True
    old = (await get(env, repo_id)).json()
    assert old["status"]["freshness"] == "stale" and old["diagram"]["repository_commit_sha"] == SHA_A

    assert (await post(env, repo_id)).status_code == 202  # a stale result is replaced on request
    assert await run_diagram(env, repo_id, FakeGenerator(VALID_RESPONSE)) == "completed"
    fresh = (await get(env, repo_id)).json()
    assert fresh["status"]["freshness"] == "current" and fresh["diagram"]["repository_commit_sha"] == SHA_B


async def test_a_changed_deterministic_analyzer_version_makes_the_diagram_stale(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_id = await repo_with(env)
    assert await run_diagram(env, repo_id, FakeGenerator(VALID_RESPONSE)) == "completed"
    assert (await get(env, repo_id, "/status")).json()["freshness"] == "current"

    from apps.api.models.entities import ArchitectureAnalysis

    await env["db"].execute(update(ArchitectureAnalysis).values(analyzer_version=999))
    await env["db"].commit()
    assert (await get(env, repo_id, "/status")).json()["freshness"] == "stale"


# --- isolation, concurrency -----------------------------------------------------------------------------------------


async def test_repositories_do_not_see_each_others_diagrams(env: dict) -> None:
    first = await repo_with(env, WEB, name="octo/first")
    second = await repo_with(env, {"only.py": ("import os\n", "python"), "pyproject.toml": ('[project]\nname="x"\n', "toml")}, name="octo/second")
    await run_diagram(env, first, FakeGenerator(VALID_RESPONSE))
    other_response = json.dumps({"groups": [], "relationships": [], "components": [{"id": "x1", "label": "Only", "description": "x", "kind": "library", "source_paths": ["only.py"]}]})
    await run_diagram(env, second, FakeGenerator(other_response))

    first_diagram = (await get(env, first)).json()["diagram"]
    second_diagram = (await get(env, second)).json()["diagram"]
    assert {c["id"] for c in first_diagram["components"]} == {"c1", "c2"}
    assert {c["id"] for c in second_diagram["components"]} == {"x1"}


async def test_a_running_generation_is_not_started_twice(env: dict) -> None:
    repo_id = await repo_with(env)
    await env["db"].execute(ArchitectureDiagramRecord.__table__.insert().values(repository_id=repo_id, status="running", target_commit_sha=SHA_A))
    await env["db"].commit()
    assert await run_diagram(env, repo_id, FakeGenerator(VALID_RESPONSE)) == "skipped"
    assert (await post(env, repo_id)).json()["enqueued"] is False


# --- queue wiring and the RQ entry point --------------------------------------------------------------------------


def test_enqueue_architecture_diagram_uses_the_shared_queue_with_retries() -> None:
    queue = MagicMock()
    enqueue_architecture_diagram(queue, 7)
    function, repository_id = queue.enqueue.call_args.args
    assert f"{function.__module__}.{function.__name__}" == ARCHITECTURE_DIAGRAM_JOB_FUNC and repository_id == 7
    assert queue.enqueue.call_args.kwargs["job_timeout"] == get_settings().ingestion_job_timeout_seconds
    assert queue.enqueue.call_args.kwargs["retry"] is not None  # unlike the deterministic job: this one can retry


async def test_the_job_runs_the_generation_and_disposes_its_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def fake_run(sessions, repository_id, settings):
        calls.append(repository_id)

    monkeypatch.setattr(architecture_diagram_job, "run_diagram", fake_run)
    await architecture_diagram_job._run(42)
    assert calls == [42]
