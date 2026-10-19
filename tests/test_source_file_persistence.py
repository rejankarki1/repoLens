"""Integration tests for source-file persistence against the disposable `repolens_test` database."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.models.entities import Repository, SourceFile
from apps.api.services.archive_reader import ArchiveReadResult, Category, SourceFile as ArchiveFile
from apps.api.services.source_file_persistence import (
    IncompleteSnapshotError,
    UnknownRepositoryError,
    persist_source_snapshot,
)

pytestmark = pytest.mark.anyio

SHA_A = "a" * 40
SHA_B = "b" * 40


def _file(path: str, content: str = "print('hi')\n", category: Category = Category.SOURCE) -> ArchiveFile:
    return ArchiveFile(
        path=path,
        language="python",
        category=category,
        content=content,
        sha256=f"{abs(hash(content)):064x}"[:64],
        size_bytes=len(content.encode()),
        line_count=content.count("\n"),
    )


def _result(
    sha: str, entries: list[ArchiveFile], *, truncated: bool = False, limit_hit: str | None = None
) -> ArchiveReadResult:
    return ArchiveReadResult(
        commit_sha=sha,
        entries=tuple(entries),
        skipped=(),
        skip_counts={},
        truncated=truncated,
        limit_hit=limit_hit,
        total_bytes=sum(e.size_bytes for e in entries),
    )


_next_github_id = 900_000


async def _make_repository(db: AsyncSession) -> Repository:
    global _next_github_id
    _next_github_id += 1
    repo = Repository(github_repo_id=_next_github_id, full_name="octo/demo", default_branch="main")
    db.add(repo)
    await db.commit()
    return repo


async def _stored(db: AsyncSession, repository_id: int) -> list[SourceFile]:
    rows = await db.execute(
        select(SourceFile).where(SourceFile.repository_id == repository_id).order_by(SourceFile.path)
    )
    return list(rows.scalars())


async def test_initial_persistence_saves_rows_and_metadata(real_db_session: AsyncSession) -> None:
    repo = await _make_repository(real_db_session)
    entries = [_file("src/app.py", "a = 1\nb = 2\n"), _file("tests/test_app.py", "x\n", Category.TEST)]

    saved = await persist_source_snapshot(real_db_session, repo.id, _result(SHA_A, entries))

    assert saved == 2
    rows = await _stored(real_db_session, repo.id)
    assert [r.path for r in rows] == ["src/app.py", "tests/test_app.py"]
    first = rows[0]
    assert first.repository_id == repo.id
    assert first.commit_sha == SHA_A
    assert first.language == "python"
    assert first.category == "source"
    assert first.content == "a = 1\nb = 2\n"
    assert first.content_hash == entries[0].sha256
    assert first.size_bytes == entries[0].size_bytes
    assert first.line_count == 2
    assert rows[1].category == "test"

    await real_db_session.refresh(repo)
    assert repo.last_ingested_commit_sha == SHA_A
    assert repo.ingestion_status == "completed"
    assert repo.ingestion_truncated is False
    assert repo.ingestion_error is None
    assert repo.last_ingested_at is not None


async def test_reingesting_same_commit_creates_no_duplicates(real_db_session: AsyncSession) -> None:
    repo = await _make_repository(real_db_session)
    result = _result(SHA_A, [_file("a.py"), _file("b.py")])

    await persist_source_snapshot(real_db_session, repo.id, result)
    await persist_source_snapshot(real_db_session, repo.id, result)

    assert len(await _stored(real_db_session, repo.id)) == 2


async def test_new_commit_replaces_old_snapshot_and_clears_error(real_db_session: AsyncSession) -> None:
    repo = await _make_repository(real_db_session)
    await persist_source_snapshot(real_db_session, repo.id, _result(SHA_A, [_file("old.py"), _file("keep.py")]))
    repo.ingestion_status = "failed"
    repo.ingestion_error = "boom"
    await real_db_session.commit()

    await persist_source_snapshot(real_db_session, repo.id, _result(SHA_B, [_file("keep.py", "new\n"), _file("new.py")]))

    rows = await _stored(real_db_session, repo.id)
    assert [r.path for r in rows] == ["keep.py", "new.py"]
    assert {r.commit_sha for r in rows} == {SHA_B}
    assert rows[0].content == "new\n"
    await real_db_session.refresh(repo)
    assert repo.last_ingested_commit_sha == SHA_B
    assert repo.ingestion_status == "completed"
    assert repo.ingestion_error is None


async def test_other_repositories_are_untouched(real_db_session: AsyncSession) -> None:
    repo_one = await _make_repository(real_db_session)
    repo_two = await _make_repository(real_db_session)
    await persist_source_snapshot(real_db_session, repo_one.id, _result(SHA_A, [_file("one.py")]))

    await persist_source_snapshot(real_db_session, repo_two.id, _result(SHA_B, [_file("two.py")]))

    assert [r.path for r in await _stored(real_db_session, repo_one.id)] == ["one.py"]


async def test_failed_save_rolls_back_and_keeps_old_snapshot(real_db_session: AsyncSession) -> None:
    repo = await _make_repository(real_db_session)
    repo_id = repo.id  # a rollback expires `repo`, so keep the ID
    await persist_source_snapshot(real_db_session, repo_id, _result(SHA_A, [_file("old.py", "old\n")]))
    # Duplicate path in one result violates the unique constraint partway through the save.
    bad = _result(SHA_B, [_file("dup.py"), _file("dup.py")])

    with pytest.raises(Exception):
        await persist_source_snapshot(real_db_session, repo_id, bad)

    rows = await _stored(real_db_session, repo_id)
    assert [(r.path, r.commit_sha, r.content) for r in rows] == [("old.py", SHA_A, "old\n")]
    await real_db_session.refresh(repo)
    assert repo.last_ingested_commit_sha == SHA_A
    assert repo.ingestion_status == "completed"


async def test_truncated_result_is_rejected_without_changes(real_db_session: AsyncSession) -> None:
    repo = await _make_repository(real_db_session)
    await persist_source_snapshot(real_db_session, repo.id, _result(SHA_A, [_file("old.py")]))
    truncated = _result(SHA_B, [_file("new.py")], truncated=True, limit_hit="max_files_accepted")

    with pytest.raises(IncompleteSnapshotError):
        await persist_source_snapshot(real_db_session, repo.id, truncated)

    assert [r.path for r in await _stored(real_db_session, repo.id)] == ["old.py"]
    await real_db_session.refresh(repo)
    assert repo.last_ingested_commit_sha == SHA_A
    assert repo.ingestion_truncated is False


async def test_unknown_repository_raises_and_stores_nothing(real_db_session: AsyncSession) -> None:
    before = (await real_db_session.execute(select(func.count()).select_from(SourceFile))).scalar_one()

    with pytest.raises(UnknownRepositoryError):
        await persist_source_snapshot(real_db_session, 999_999_999, _result(SHA_A, [_file("a.py")]))

    after = (await real_db_session.execute(select(func.count()).select_from(SourceFile))).scalar_one()
    assert after == before
