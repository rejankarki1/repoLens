"""Chunk persistence and the worker flow, against the disposable `repolens_test` database.

Covers: files and chunks written together, source references, re-ingestion, duplicate prevention,
rollback that preserves the previous snapshot, the text fallback, parser failures, and that
chunking runs off the event loop.
"""

import dataclasses
import hashlib
import io
import logging
import threading
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import tree_sitter_language_pack as tslp
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import apps.api.services.code_indexing as indexing_module
import apps.api.services.ingestion as ingestion_module
import apps.api.services.source_file_persistence as persistence_module
from apps.api.jobs.ingestion_job import run_ingestion
from apps.api.models.entities import CodeChunk, Repository, SourceFile
from apps.api.services.archive_reader import ArchiveReadResult, Category
from apps.api.services.archive_reader import SourceFile as ArchiveFile
from apps.api.services.code_indexing import index_entries
from apps.api.services.code_parser import CodeChunk as ParsedChunk
from apps.api.services.code_parser import ensure_grammars, missing_grammars
from apps.api.services.github_archive import ArchiveDownloadResult
from apps.api.services.ingestion import request_ingestion
from apps.api.services.source_file_persistence import IncompleteSnapshotError, chunk_row, persist_source_snapshot

pytestmark = pytest.mark.anyio

SHA_A, SHA_B = "a" * 40, "b" * 40
_github_ids = iter(range(100_000, 200_000))
PY = "import os\n\nclass A:\n    def m(self):\n        return 1\n\ndef f():\n    return 2\n"
TS = "export interface User { id: number }\nexport function helper(x: number): number { return x; }\n"
GO = 'package main\n\nfunc main() {\n    println("hi")\n}\n'


@pytest.fixture(scope="module", autouse=True)
def _grammars_available() -> None:
    if missing_grammars():
        try:
            ensure_grammars()
        except Exception as exc:
            pytest.skip(f"tree-sitter grammars unavailable: {exc}")


def _entry(path: str, content: str, language: str = "unknown") -> ArchiveFile:
    return ArchiveFile(
        path=path, language=language, category=Category.SOURCE, content=content,
        sha256=hashlib.sha256(content.encode()).hexdigest(), size_bytes=len(content.encode()),
        line_count=content.count("\n"),
    )


def _result(sha: str, files: dict[str, tuple[str, str]], truncated: bool = False) -> ArchiveReadResult:
    entries = tuple(_entry(path, content, language) for path, (content, language) in files.items())
    return ArchiveReadResult(sha, entries, (), {}, truncated, "max_files" if truncated else None, 0)


async def _repository(db: AsyncSession, status: str = "pending") -> int:
    repo = Repository(github_repo_id=next(_github_ids), full_name="octo/demo", default_branch="main", ingestion_status=status)
    db.add(repo)
    await db.commit()
    return repo.id


async def _persist(db: AsyncSession, repo_id: int, result: ArchiveReadResult) -> int:
    return await persist_source_snapshot(db, repo_id, result, index_entries(result.entries).chunks_by_path)


async def _files(db: AsyncSession, repo_id: int) -> dict[str, SourceFile]:
    rows = await db.execute(select(SourceFile).where(SourceFile.repository_id == repo_id).execution_options(populate_existing=True))
    return {f.path: f for f in rows.scalars()}


async def _chunks(db: AsyncSession, repo_id: int) -> list[tuple[str, CodeChunk]]:
    rows = await db.execute(
        select(SourceFile.path, CodeChunk)
        .join(CodeChunk, CodeChunk.source_file_id == SourceFile.id)
        .where(SourceFile.repository_id == repo_id)
        .order_by(SourceFile.path, CodeChunk.chunk_index)
        .execution_options(populate_existing=True)
    )
    return [(path, chunk) for path, chunk in rows.all()]


async def _state(db: AsyncSession, repo_id: int) -> Repository:
    return (await db.execute(select(Repository).where(Repository.id == repo_id).execution_options(populate_existing=True))).scalar_one()


FILES = {"app.py": (PY, "python"), "lib.ts": (TS, "typescript"), "main.go": (GO, "go")}


# --- persistence: files and chunks together --------------------------------------------------------


async def test_chunks_are_stored_with_their_files_and_point_at_the_right_source(real_db_session: AsyncSession) -> None:
    db = real_db_session
    repo_id = await _repository(db)

    stored = await _persist(db, repo_id, _result(SHA_A, FILES))

    assert stored == 3
    files, chunks = await _files(db, repo_id), await _chunks(db, repo_id)
    assert {path for path, _ in chunks} == {"app.py", "lib.ts", "main.go"}
    for path, content in ((p, c) for p, (c, _l) in FILES.items()):
        mine = [c for p, c in chunks if p == path]
        assert [c.chunk_index for c in mine] == list(range(len(mine)))
        assert all(c.source_file_id == files[path].id for c in mine)  # the right file, in this snapshot
        assert files[path].repository_id == repo_id and files[path].commit_sha == SHA_A
        data = files[path].content.encode()
        assert "".join(c.content for c in mine) == content
        assert all(data[c.start_byte : c.end_byte].decode() == c.content for c in mine)  # offsets index the file
        assert all(c.content_hash == hashlib.sha256(c.content.encode()).hexdigest() for c in mine)
    by_path = {p: c for p, c in chunks}
    assert (by_path["app.py"].language, by_path["app.py"].parser_mode, by_path["app.py"].symbols) == ("python", "ast", ["A", "m", "f"])
    assert (by_path["main.go"].language, by_path["main.go"].parser_mode, by_path["main.go"].kind) == ("go", "fallback", "text")
    assert (await _state(db, repo_id)).last_ingested_commit_sha == SHA_A


async def test_persisting_without_chunks_still_works_as_before(real_db_session: AsyncSession) -> None:
    db = real_db_session
    repo_id = await _repository(db)

    assert await persist_source_snapshot(db, repo_id, _result(SHA_A, FILES)) == 3

    assert len(await _files(db, repo_id)) == 3 and await _chunks(db, repo_id) == []


async def test_reingesting_the_same_commit_replaces_chunks_without_duplicates(real_db_session: AsyncSession) -> None:
    db = real_db_session
    repo_id = await _repository(db)
    await _persist(db, repo_id, _result(SHA_A, FILES))
    first = await _chunks(db, repo_id)

    await _persist(db, repo_id, _result(SHA_A, FILES))
    second = await _chunks(db, repo_id)

    assert len(second) == len(first)
    assert [(p, c.chunk_index, c.content_hash) for p, c in second] == [(p, c.chunk_index, c.content_hash) for p, c in first]
    assert {c.id for _, c in second}.isdisjoint({c.id for _, c in first})  # replaced, not appended
    total = (await db.execute(select(func.count()).select_from(CodeChunk).join(SourceFile).where(SourceFile.repository_id == repo_id))).scalar_one()
    assert total == len(first)


async def test_a_new_commit_replaces_outdated_files_and_chunks(real_db_session: AsyncSession) -> None:
    db = real_db_session
    repo_id = await _repository(db)
    await _persist(db, repo_id, _result(SHA_A, FILES))

    await _persist(db, repo_id, _result(SHA_B, {"app.py": ("x = 1\n", "python"), "new.py": ("y = 2\n", "python")}))

    chunks = await _chunks(db, repo_id)
    assert {p for p, _ in chunks} == {"app.py", "new.py"}  # lib.ts / main.go and their chunks are gone
    assert [c.content for p, c in chunks if p == "app.py"] == ["x = 1\n"]
    commits = (await db.execute(select(SourceFile.commit_sha).where(SourceFile.repository_id == repo_id))).scalars().all()
    assert set(commits) == {SHA_B}


async def test_an_unchanged_commit_without_chunks_gets_indexed_on_reingestion(real_db_session: AsyncSession) -> None:
    db = real_db_session
    repo_id = await _repository(db)
    await persist_source_snapshot(db, repo_id, _result(SHA_A, FILES))  # a snapshot stored before chunks existed
    assert await _chunks(db, repo_id) == []

    await _persist(db, repo_id, _result(SHA_A, FILES))  # same commit again

    assert {p for p, _ in await _chunks(db, repo_id)} == set(FILES)


async def test_failed_chunk_write_rolls_back_and_keeps_the_previous_snapshot(real_db_session: AsyncSession) -> None:
    db = real_db_session
    repo_id = await _repository(db)
    await _persist(db, repo_id, _result(SHA_A, FILES))
    before = [(p, c.chunk_index, c.content_hash, c.id) for p, c in await _chunks(db, repo_id)]
    files_before = {p: f.id for p, f in (await _files(db, repo_id)).items()}

    new = _result(SHA_B, {"app.py": ("x = 1\n", "python"), "z.py": ("z = 1\n", "python")})
    chunks = index_entries(new.entries).chunks_by_path
    chunks["z.py"] = tuple(dataclasses.replace(c, end_byte=c.start_byte) for c in chunks["z.py"])  # violates a CHECK
    with pytest.raises(IntegrityError):
        await persist_source_snapshot(db, repo_id, new, chunks)

    assert [(p, c.chunk_index, c.content_hash, c.id) for p, c in await _chunks(db, repo_id)] == before
    assert {p: f.id for p, f in (await _files(db, repo_id)).items()} == files_before
    state = await _state(db, repo_id)
    assert state.last_ingested_commit_sha == SHA_A


async def test_chunks_for_unknown_files_are_rejected_before_anything_changes(real_db_session: AsyncSession) -> None:
    db = real_db_session
    repo_id = await _repository(db)
    await _persist(db, repo_id, _result(SHA_A, FILES))
    before = len(await _chunks(db, repo_id))
    stray = index_entries([_entry("ghost.py", "g = 1\n", "python")]).chunks_by_path

    with pytest.raises(ValueError, match="ghost.py"):
        await persist_source_snapshot(db, repo_id, _result(SHA_B, {"app.py": ("x = 1\n", "python")}), stray)

    assert len(await _chunks(db, repo_id)) == before and (await _state(db, repo_id)).last_ingested_commit_sha == SHA_A


async def test_truncated_snapshots_are_rejected_and_leave_chunks_alone(real_db_session: AsyncSession) -> None:
    db = real_db_session
    repo_id = await _repository(db)
    await _persist(db, repo_id, _result(SHA_A, FILES))
    before = len(await _chunks(db, repo_id))

    with pytest.raises(IncompleteSnapshotError):
        await persist_source_snapshot(db, repo_id, _result(SHA_B, FILES, truncated=True), {})

    assert len(await _chunks(db, repo_id)) == before


async def test_large_snapshots_are_written_in_one_batched_call(real_db_session: AsyncSession) -> None:
    db = real_db_session
    repo_id = await _repository(db)
    files = {f"m{i}.py": ("".join(f"def f{j}(x):\n    return x + {j}\n\n" for j in range(120)), "python") for i in range(60)}
    result = _result(SHA_A, files)
    index = index_entries(result.entries, chunk_max_bytes=64)
    assert index.stats.chunks > 3_000  # well past one SQL statement worth of rows

    await persist_source_snapshot(db, repo_id, result, index.chunks_by_path)

    assert len(await _chunks(db, repo_id)) == index.stats.chunks


def test_row_values_are_bounded_to_their_column_sizes() -> None:
    long = ParsedChunk(
        language="x" * 100, kind="k" * 50, symbol="s" * 400, symbols=("s" * 400,), start_line=1, end_line=1,
        start_byte=0, end_byte=3, content="abc", parser="ast", starts_mid_line=False, ends_mid_line=False,
    )

    row = chunk_row(1, 0, long)

    assert (len(row["language"]), len(row["kind"]), len(row["symbol_name"])) == (64, 32, 255)  # type: ignore[arg-type]


# --- the worker flow ----------------------------------------------------------------------------------


def _zip(directory: Path, files: dict[str, str], name: str = "a.zip") -> Path:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for path, content in files.items():
            zf.writestr(f"octo-demo-abc/{path}", content)
    path = directory / name
    path.write_bytes(buffer.getvalue())
    return path


def _serve(archives: AsyncMock, path: Path, sha: str) -> None:
    archives.fetch_repository_archive.return_value = ArchiveDownloadResult(archive_path=path, commit_sha=sha)


async def _ingest(db: AsyncSession, repo_id: int, archives: AsyncMock) -> None:
    await request_ingestion(db, repo_id)
    await run_ingestion(db, repo_id, archives)


WORKER_FILES = {"src/app.py": PY, "web/lib.ts": TS, "cmd/main.go": GO, "README.md": "# Demo\n\nHello.\n"}


async def test_ingestion_stores_files_and_chunks_then_completes(
    real_db_session: AsyncSession, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    db, archives = real_db_session, AsyncMock()
    repo_id = await _repository(db)
    _serve(archives, _zip(tmp_path, WORKER_FILES), SHA_A)

    with caplog.at_level(logging.INFO, logger="apps.api.services.ingestion"):
        await _ingest(db, repo_id, archives)

    state = await _state(db, repo_id)
    assert (state.ingestion_status, state.last_ingested_commit_sha, state.ingestion_heartbeat_at) == ("completed", SHA_A, None)
    chunks = await _chunks(db, repo_id)
    assert {p for p, _ in chunks} == set(WORKER_FILES)
    modes = {p: c.parser_mode for p, c in chunks}
    assert modes == {"src/app.py": "ast", "web/lib.ts": "ast", "cmd/main.go": "fallback", "README.md": "fallback"}
    assert "Indexed repository" in caplog.text and f"{len(chunks)} chunks" in caplog.text  # basic metrics are logged


async def test_chunking_runs_off_the_event_loop(real_db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db, archives = real_db_session, AsyncMock()
    repo_id = await _repository(db)
    _serve(archives, _zip(tmp_path, WORKER_FILES), SHA_A)
    threads: list[str] = []
    real = ingestion_module.index_entries

    def spy(entries, **kwargs):  # type: ignore[no-untyped-def]
        threads.append(threading.current_thread().name)
        return real(entries, **kwargs)

    monkeypatch.setattr(ingestion_module, "index_entries", spy)

    await _ingest(db, repo_id, archives)

    assert threads and threads[0] != threading.main_thread().name  # a worker thread, so the heartbeat keeps running


async def test_reingestion_through_the_worker_has_no_duplicate_chunks(real_db_session: AsyncSession, tmp_path: Path) -> None:
    db, archives = real_db_session, AsyncMock()
    repo_id = await _repository(db)
    _serve(archives, _zip(tmp_path, WORKER_FILES, "one.zip"), SHA_A)
    await _ingest(db, repo_id, archives)
    count = len(await _chunks(db, repo_id))

    _serve(archives, _zip(tmp_path, WORKER_FILES, "two.zip"), SHA_A)  # re-analyze the same commit
    await _ingest(db, repo_id, archives)

    assert len(await _chunks(db, repo_id)) == count and (await _state(db, repo_id)).ingestion_status == "completed"


async def test_one_unparseable_file_does_not_fail_the_ingestion(
    real_db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, archives = real_db_session, AsyncMock()
    repo_id = await _repository(db)
    _serve(archives, _zip(tmp_path, {**WORKER_FILES, "bad.py": "BOOM = 1\n"}), SHA_A)
    real = indexing_module.chunk_source

    def flaky(source: str, language: str | None, **kwargs: object):  # type: ignore[no-untyped-def]
        if "BOOM" in source:
            raise RuntimeError("parser exploded")
        return real(source, language, **kwargs)

    monkeypatch.setattr(indexing_module, "chunk_source", flaky)

    await _ingest(db, repo_id, archives)

    assert (await _state(db, repo_id)).ingestion_status == "completed"
    assert "bad.py" in await _files(db, repo_id)  # the file is stored, just without chunks
    assert "bad.py" not in {p for p, _ in await _chunks(db, repo_id)}
    assert {p for p, _ in await _chunks(db, repo_id)} == set(WORKER_FILES)


async def test_missing_grammars_use_the_text_fallback_and_never_download(
    real_db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, archives = real_db_session, AsyncMock()
    repo_id = await _repository(db)
    _serve(archives, _zip(tmp_path, WORKER_FILES), SHA_A)

    def forbidden(*_a: object, **_k: object) -> None:
        pytest.fail("a grammar must never be downloaded while processing a repository")

    for name in ("process", "download", "download_all", "download_group", "init", "prefetch", "get_parser"):
        monkeypatch.setattr(tslp, name, forbidden)
    monkeypatch.setattr(tslp, "downloaded_languages", lambda: [])

    await _ingest(db, repo_id, archives)

    assert (await _state(db, repo_id)).ingestion_status == "completed"
    assert {c.parser_mode for _, c in await _chunks(db, repo_id)} == {"fallback"}


async def test_a_failed_reingestion_keeps_the_previous_files_and_chunks(
    real_db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, archives = real_db_session, AsyncMock()
    repo_id = await _repository(db)
    _serve(archives, _zip(tmp_path, WORKER_FILES, "one.zip"), SHA_A)
    await _ingest(db, repo_id, archives)
    before = [(p, c.chunk_index, c.content_hash, c.id) for p, c in await _chunks(db, repo_id)]

    def broken_row(*_a: object, **_k: object) -> dict[str, object]:
        raise RuntimeError("could not build a chunk row")

    monkeypatch.setattr(persistence_module, "chunk_row", broken_row)
    _serve(archives, _zip(tmp_path, {"new.py": "n = 1\n"}, "two.zip"), SHA_B)
    await _ingest(db, repo_id, archives)

    state = await _state(db, repo_id)
    assert (state.ingestion_status, state.last_ingested_commit_sha) == ("failed", SHA_A)
    assert state.ingestion_error == "Unexpected error during ingestion"
    assert [(p, c.chunk_index, c.content_hash, c.id) for p, c in await _chunks(db, repo_id)] == before
    assert set(await _files(db, repo_id)) == set(WORKER_FILES)


async def test_a_legacy_snapshot_without_chunks_is_indexed_by_reanalyzing(real_db_session: AsyncSession, tmp_path: Path) -> None:
    db, archives = real_db_session, AsyncMock()
    repo_id = await _repository(db)
    await persist_source_snapshot(db, repo_id, _result(SHA_A, {p: (c, "unknown") for p, c in WORKER_FILES.items()}))
    assert await _chunks(db, repo_id) == []
    _serve(archives, _zip(tmp_path, WORKER_FILES), SHA_A)

    await _ingest(db, repo_id, archives)  # same commit, now with chunking

    assert {p for p, _ in await _chunks(db, repo_id)} == set(WORKER_FILES)
