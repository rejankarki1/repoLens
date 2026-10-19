"""Tests for the file explorer endpoints against the disposable `repolens_test` database."""

import io
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.main import app
from apps.api.models.entities import Repository, SourceFile
from apps.api.services.archive_reader import ArchiveReadResult, Category, SourceFile as ArchiveFile
from apps.api.services.github_archive import ArchiveDownloadResult, get_archive_service
from apps.api.services.source_file_persistence import persist_source_snapshot

pytestmark = pytest.mark.anyio

SHA_OLD = "1" * 40
SHA_NEW = "2" * 40
_github_ids = iter(range(600_000, 700_000))


def _entry(path: str, content: str, category: Category = Category.SOURCE, language: str = "python") -> ArchiveFile:
    return ArchiveFile(
        path=path,
        language=language,
        category=category,
        content=content,
        sha256="f" * 64,
        size_bytes=len(content.encode()),
        line_count=content.count("\n"),
    )


async def _repository(db: AsyncSession, entries: list[ArchiveFile] | None = None, sha: str = SHA_NEW) -> int:
    repo = Repository(github_repo_id=next(_github_ids), full_name="octo/demo", default_branch="main")
    db.add(repo)
    await db.commit()
    repo_id = repo.id
    if entries is not None:
        result = ArchiveReadResult(
            commit_sha=sha,
            entries=tuple(entries),
            skipped=(),
            skip_counts={},
            truncated=False,
            limit_hit=None,
            total_bytes=0,
        )
        await persist_source_snapshot(db, repo_id, result)
    return repo_id


async def test_list_files_returns_sorted_metadata_without_content(
    real_client: httpx.AsyncClient, real_db_session: AsyncSession
) -> None:
    repo_id = await _repository(
        real_db_session,
        [
            _entry("src/b.py", "b = 2\nc = 3\n"),
            _entry("README.md", "# hi\n", Category.DOCS, "markdown"),
            _entry("src/a.py", "a = 1\n"),
            _entry("tests/test_a.py", "assert True\n", Category.TEST),
        ],
    )

    response = await real_client.get(f"/repositories/{repo_id}/files")

    assert response.status_code == 200
    body = response.json()
    assert [f["path"] for f in body] == ["README.md", "src/a.py", "src/b.py", "tests/test_a.py"]
    assert body[2] == {"path": "src/b.py", "language": "python", "category": "source", "size_bytes": 12, "line_count": 2}
    assert body[0]["category"] == "docs" and body[0]["language"] == "markdown"
    assert all(set(f) == {"path", "language", "category", "size_bytes", "line_count"} for f in body)


async def test_list_files_is_empty_for_never_ingested_repository(
    real_client: httpx.AsyncClient, real_db_session: AsyncSession
) -> None:
    repo_id = await _repository(real_db_session)

    response = await real_client.get(f"/repositories/{repo_id}/files")

    assert response.status_code == 200
    assert response.json() == []


async def test_unknown_repository_returns_404_for_both_endpoints(real_client: httpx.AsyncClient) -> None:
    assert (await real_client.get("/repositories/999999999/files")).status_code == 404
    response = await real_client.get("/repositories/999999999/files/content", params={"path": "a.py"})
    assert response.status_code == 404


async def test_get_file_content(real_client: httpx.AsyncClient, real_db_session: AsyncSession) -> None:
    repo_id = await _repository(real_db_session, [_entry("src/app.py", "print('hi')\n"), _entry("b.py", "b\n")])

    response = await real_client.get(f"/repositories/{repo_id}/files/content", params={"path": "src/app.py"})

    assert response.status_code == 200
    assert response.json() == {
        "path": "src/app.py",
        "language": "python",
        "category": "source",
        "size_bytes": 12,
        "line_count": 1,
        "content": "print('hi')\n",
    }


async def test_missing_file_returns_404(real_client: httpx.AsyncClient, real_db_session: AsyncSession) -> None:
    repo_id = await _repository(real_db_session, [_entry("a.py", "a\n")])

    missing = await real_client.get(f"/repositories/{repo_id}/files/content", params={"path": "nope.py"})
    traversal = await real_client.get(f"/repositories/{repo_id}/files/content", params={"path": "../../etc/passwd"})

    assert missing.status_code == 404
    assert traversal.status_code == 404  # only ever a database lookup, never a filesystem read


async def test_content_requires_a_path(real_client: httpx.AsyncClient, real_db_session: AsyncSession) -> None:
    repo_id = await _repository(real_db_session, [_entry("a.py", "a\n")])

    assert (await real_client.get(f"/repositories/{repo_id}/files/content")).status_code == 422
    assert (await real_client.get(f"/repositories/{repo_id}/files/content", params={"path": ""})).status_code == 422


async def test_content_of_never_ingested_repository_is_404(
    real_client: httpx.AsyncClient, real_db_session: AsyncSession
) -> None:
    repo_id = await _repository(real_db_session)

    response = await real_client.get(f"/repositories/{repo_id}/files/content", params={"path": "a.py"})

    assert response.status_code == 404


async def test_files_from_other_commits_are_not_exposed(
    real_client: httpx.AsyncClient, real_db_session: AsyncSession
) -> None:
    repo_id = await _repository(real_db_session, [_entry("current.py", "new\n"), _entry("shared.py", "new shared\n")])
    # A leftover row from some other commit must never be served.
    for path, content in (("stale.py", "old\n"), ("shared.py", "old shared\n")):
        real_db_session.add(
            SourceFile(
                repository_id=repo_id,
                commit_sha=SHA_OLD,
                path=path,
                language="python",
                category="source",
                content=content,
                content_hash="e" * 64,
                size_bytes=len(content),
                line_count=1,
            )
        )
    await real_db_session.commit()

    listing = await real_client.get(f"/repositories/{repo_id}/files")
    stale = await real_client.get(f"/repositories/{repo_id}/files/content", params={"path": "stale.py"})
    shared = await real_client.get(f"/repositories/{repo_id}/files/content", params={"path": "shared.py"})

    assert [f["path"] for f in listing.json()] == ["current.py", "shared.py"]
    assert stale.status_code == 404
    assert shared.json()["content"] == "new shared\n"


async def test_files_available_after_real_ingestion_flow(
    real_client: httpx.AsyncClient, real_db_session: AsyncSession, tmp_path: Path
) -> None:
    repo_id = await _repository(real_db_session)
    archives = AsyncMock()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("octo-demo-abc/src/app.py", "x = 1\n")
        zf.writestr("octo-demo-abc/README.md", "# demo\n")
    zip_path = tmp_path / "a.zip"
    zip_path.write_bytes(buffer.getvalue())
    archives.fetch_repository_archive.return_value = ArchiveDownloadResult(archive_path=zip_path, commit_sha=SHA_NEW)

    async def override() -> AsyncIterator[AsyncMock]:
        yield archives

    app.dependency_overrides[get_archive_service] = override
    try:
        assert (await real_client.post(f"/repositories/{repo_id}/ingestions")).status_code == 200
    finally:
        app.dependency_overrides.pop(get_archive_service, None)

    listing = await real_client.get(f"/repositories/{repo_id}/files")
    content = await real_client.get(f"/repositories/{repo_id}/files/content", params={"path": "src/app.py"})

    assert [f["path"] for f in listing.json()] == ["README.md", "src/app.py"]
    assert content.json()["content"] == "x = 1\n"
