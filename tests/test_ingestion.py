"""Tests for the ingestion endpoints against the disposable `repolens_test` database.

Only the GitHub archive service is faked (it returns a real ZIP on disk); archive reading and
PostgreSQL persistence are real.
"""

import io
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

import apps.api.services.ingestion as ingestion_module
from apps.api.main import app
from apps.api.models.entities import Repository, SourceFile
from apps.api.services.github import GitHubRateLimitedError, GitHubTimeoutError, GitHubUpstreamError
from apps.api.services.github_archive import ArchiveDownloadResult, MalformedArchiveError, get_archive_service

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
async def ingest_client(real_client: httpx.AsyncClient, archives: AsyncMock) -> AsyncIterator[httpx.AsyncClient]:
    async def override() -> AsyncIterator[AsyncMock]:
        yield archives

    app.dependency_overrides[get_archive_service] = override
    try:
        yield real_client
    finally:
        app.dependency_overrides.pop(get_archive_service, None)


async def _make_repository(db: AsyncSession) -> int:
    repo = Repository(github_repo_id=next(_github_ids), full_name="octo/demo", default_branch="main")
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


async def test_successful_ingestion(
    ingest_client: httpx.AsyncClient, real_db_session: AsyncSession, archives: AsyncMock, tmp_path: Path
) -> None:
    repo_id = await _make_repository(real_db_session)
    _serve(archives, _write_zip(tmp_path, {"src/app.py": "x = 1\n", "README.md": "# hi\n"}), SHA_A)

    response = await ingest_client.post(f"/repositories/{repo_id}/ingestions")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["commit_sha"] == SHA_A
    assert body["truncated"] is False
    assert body["files_stored"] == 2
    assert body["last_ingested_at"] is not None
    assert body["error"] is None
    archives.fetch_repository_archive.assert_awaited_once_with("octo", "demo")
    assert await _paths(real_db_session, repo_id) == [("README.md", SHA_A), ("src/app.py", SHA_A)]

    status = await ingest_client.get(f"/repositories/{repo_id}/ingestion-status")
    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    assert status.json()["commit_sha"] == SHA_A


async def test_unknown_repository_returns_404(ingest_client: httpx.AsyncClient, archives: AsyncMock) -> None:
    response = await ingest_client.post("/repositories/999999999/ingestions")

    assert response.status_code == 404
    archives.fetch_repository_archive.assert_not_awaited()
    assert (await ingest_client.get("/repositories/999999999/ingestion-status")).status_code == 404


@pytest.mark.parametrize(
    ("error", "status_code", "message"),
    [
        (GitHubRateLimitedError("secret token abc"), 429, "GitHub rate limit exceeded; try again later"),
        (GitHubTimeoutError("internal url"), 504, "Timed out contacting GitHub"),
        (GitHubUpstreamError("internal detail"), 502, "GitHub returned an unexpected response"),
    ],
)
async def test_github_download_failure_marks_failed(
    ingest_client: httpx.AsyncClient,
    real_db_session: AsyncSession,
    archives: AsyncMock,
    error: Exception,
    status_code: int,
    message: str,
) -> None:
    repo_id = await _make_repository(real_db_session)
    archives.fetch_repository_archive.side_effect = error

    response = await ingest_client.post(f"/repositories/{repo_id}/ingestions")

    assert response.status_code == status_code
    assert response.json()["detail"] == message
    state = await _repo_state(real_db_session, repo_id)
    assert state.ingestion_status == "failed"
    assert state.ingestion_error == message
    assert "secret" not in state.ingestion_error and "internal" not in state.ingestion_error


async def test_archive_read_failure_cleans_up_and_marks_failed(
    ingest_client: httpx.AsyncClient, real_db_session: AsyncSession, archives: AsyncMock, tmp_path: Path
) -> None:
    repo_id = await _make_repository(real_db_session)
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    _serve(archives, bad, SHA_A)

    response = await ingest_client.post(f"/repositories/{repo_id}/ingestions")

    assert response.status_code == 422
    assert response.json()["detail"] == "Repository archive could not be read safely"
    assert not bad.exists()
    state = await _repo_state(real_db_session, repo_id)
    assert state.ingestion_status == "failed"
    assert await _paths(real_db_session, repo_id) == []


async def test_truncated_archive_is_rejected_and_marked_failed(
    ingest_client: httpx.AsyncClient,
    real_db_session: AsyncSession,
    archives: AsyncMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_id = await _make_repository(real_db_session)
    _serve(archives, _write_zip(tmp_path, {f"f{i}.py": "x\n" for i in range(3)}), SHA_A)
    real_read = ingestion_module.read_archive

    def tiny_limit(path: Path, sha: str):  # type: ignore[no-untyped-def]
        from apps.api.config import Settings

        return real_read(path, sha, settings=Settings(archive_reader_max_files_accepted=1))

    monkeypatch.setattr(ingestion_module, "read_archive", tiny_limit)

    response = await ingest_client.post(f"/repositories/{repo_id}/ingestions")

    assert response.status_code == 422
    assert "truncated" in response.json()["detail"]
    assert await _paths(real_db_session, repo_id) == []
    assert (await _repo_state(real_db_session, repo_id)).ingestion_status == "failed"


async def test_persistence_failure_keeps_previous_snapshot(
    ingest_client: httpx.AsyncClient,
    real_db_session: AsyncSession,
    archives: AsyncMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_id = await _make_repository(real_db_session)
    _serve(archives, _write_zip(tmp_path, {"old.py": "old\n"}, "one.zip"), SHA_A)
    assert (await ingest_client.post(f"/repositories/{repo_id}/ingestions")).status_code == 200

    real_persist = ingestion_module.persist_source_snapshot

    async def delete_then_explode(db: AsyncSession, repository_id: int, result: object) -> int:
        await db.execute(delete(SourceFile).where(SourceFile.repository_id == repository_id))
        raise RuntimeError("disk exploded at /secret/path")

    monkeypatch.setattr(ingestion_module, "persist_source_snapshot", delete_then_explode)
    second_zip = _write_zip(tmp_path, {"new.py": "new\n"}, "two.zip")
    _serve(archives, second_zip, SHA_B)

    response = await ingest_client.post(f"/repositories/{repo_id}/ingestions")

    assert response.status_code == 500
    assert response.json()["detail"] == "Unexpected error during ingestion"
    assert not second_zip.exists()
    assert await _paths(real_db_session, repo_id) == [("old.py", SHA_A)]
    state = await _repo_state(real_db_session, repo_id)
    assert state.ingestion_status == "failed"
    assert state.last_ingested_commit_sha == SHA_A
    assert "secret" not in state.ingestion_error

    # A later retry succeeds and replaces the snapshot.
    monkeypatch.setattr(ingestion_module, "persist_source_snapshot", real_persist)
    _serve(archives, _write_zip(tmp_path, {"new.py": "new\n"}, "three.zip"), SHA_B)
    assert (await ingest_client.post(f"/repositories/{repo_id}/ingestions")).status_code == 200
    assert await _paths(real_db_session, repo_id) == [("new.py", SHA_B)]
    assert (await _repo_state(real_db_session, repo_id)).ingestion_error is None


async def test_status_is_in_progress_while_persisting_then_completed(
    ingest_client: httpx.AsyncClient,
    real_db_session: AsyncSession,
    archives: AsyncMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_id = await _make_repository(real_db_session)
    assert (await _repo_state(real_db_session, repo_id)).ingestion_status == "pending"
    _serve(archives, _write_zip(tmp_path, {"a.py": "a\n"}), SHA_A)
    real_persist = ingestion_module.persist_source_snapshot
    seen: list[str] = []

    async def spy(db: AsyncSession, repository_id: int, result: object) -> int:
        seen.append((await _repo_state(db, repository_id)).ingestion_status)
        return await real_persist(db, repository_id, result)  # type: ignore[arg-type]

    monkeypatch.setattr(ingestion_module, "persist_source_snapshot", spy)

    await ingest_client.post(f"/repositories/{repo_id}/ingestions")

    assert seen == ["in_progress"]
    assert (await _repo_state(real_db_session, repo_id)).ingestion_status == "completed"


async def test_concurrent_ingestion_is_rejected_with_409(
    ingest_client: httpx.AsyncClient, real_db_session: AsyncSession, archives: AsyncMock
) -> None:
    repo_id = await _make_repository(real_db_session)
    (await _repo_state(real_db_session, repo_id)).ingestion_status = "in_progress"
    await real_db_session.commit()

    response = await ingest_client.post(f"/repositories/{repo_id}/ingestions")

    assert response.status_code == 409
    archives.fetch_repository_archive.assert_not_awaited()
    assert (await _repo_state(real_db_session, repo_id)).ingestion_status == "in_progress"


async def test_successful_download_zip_is_cleaned_up(
    ingest_client: httpx.AsyncClient, real_db_session: AsyncSession, archives: AsyncMock, tmp_path: Path
) -> None:
    repo_id = await _make_repository(real_db_session)
    path = _write_zip(tmp_path, {"a.py": "a\n"})
    _serve(archives, path, SHA_A)

    await ingest_client.post(f"/repositories/{repo_id}/ingestions")

    assert not path.exists()


async def test_malformed_download_error_from_github_service_is_422(
    ingest_client: httpx.AsyncClient, real_db_session: AsyncSession, archives: AsyncMock
) -> None:
    repo_id = await _make_repository(real_db_session)
    archives.fetch_repository_archive.side_effect = MalformedArchiveError("not a zip")

    response = await ingest_client.post(f"/repositories/{repo_id}/ingestions")

    assert response.status_code == 422


async def test_repository_endpoints_expose_ingestion_state(
    ingest_client: httpx.AsyncClient, real_db_session: AsyncSession, archives: AsyncMock, tmp_path: Path
) -> None:
    repo_id = await _make_repository(real_db_session)
    before = (await ingest_client.get(f"/repositories/{repo_id}")).json()
    assert before["ingestion_status"] == "pending"
    assert before["last_ingested_commit_sha"] is None

    _serve(archives, _write_zip(tmp_path, {"a.py": "a\n"}), SHA_A)
    await ingest_client.post(f"/repositories/{repo_id}/ingestions")

    detail = (await ingest_client.get(f"/repositories/{repo_id}")).json()
    listed = next(r for r in (await ingest_client.get("/repositories")).json() if r["id"] == repo_id)
    for body in (detail, listed):
        assert body["ingestion_status"] == "completed"
        assert body["last_ingested_commit_sha"] == SHA_A
        assert body["ingestion_truncated"] is False
        assert body["last_ingested_at"] is not None
        assert body["ingestion_error"] is None
