"""Model-level tests for `CodeChunk`, against the disposable `repolens_test` database.

Its schema is created from the SQLAlchemy models, so these verify the constraints, foreign keys and
cascades exactly as declared. Chunk persistence itself is not implemented yet; `_chunk_row` below
shows how a parser chunk maps onto a row.
"""

import hashlib

import pytest
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint, delete, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.models import Base
from apps.api.models.entities import CodeChunk, Repository, SourceFile
from apps.api.services import code_parser
from apps.api.services.archive_reader import ArchiveReadResult, Category
from apps.api.services.archive_reader import SourceFile as ArchiveFile
from apps.api.services.source_file_persistence import chunk_row, persist_source_snapshot

pytestmark = pytest.mark.anyio

SHA = "a" * 40
_github_ids = iter(range(200_000, 300_000))

SOURCE = '''import os

class Greeter:
    """Says hi."""
    def hello(self, name: str) -> str:
        return "héllo " + name

def main() -> None:
    print(Greeter().hello("世界"))
'''


async def _repository(db: AsyncSession) -> int:
    repo = Repository(github_repo_id=next(_github_ids), full_name="octo/demo", default_branch="main")
    db.add(repo)
    await db.commit()
    return repo.id


async def _source_file(db: AsyncSession, repository_id: int, path: str = "app.py", content: str = SOURCE) -> int:
    row = SourceFile(
        repository_id=repository_id, commit_sha=SHA, path=path, language="python", category="source",
        content=content, content_hash=hashlib.sha256(content.encode()).hexdigest(),
        size_bytes=len(content.encode()), line_count=content.count("\n"),
    )
    db.add(row)
    await db.commit()
    return row.id


def _chunk(source_file_id: int, index: int = 0, **overrides: object) -> CodeChunk:
    fields: dict[str, object] = dict(
        source_file_id=source_file_id, chunk_index=index, language="python", kind="function", symbol_name="f",
        symbols=["f"],
        parser_mode="ast", start_line=1, end_line=2, start_byte=0, end_byte=10, content="def f(): 1",
        content_hash="c" * 64,
    )
    fields.update(overrides)
    return CodeChunk(**fields)


def _chunk_row(source_file_id: int, index: int, chunk: code_parser.CodeChunk) -> CodeChunk:
    """A row built with the same mapping the persistence service uses."""
    row = chunk_row(source_file_id, index, chunk)
    return CodeChunk(**row)


async def _count(db: AsyncSession, **where: int) -> int:
    query = select(func.count()).select_from(CodeChunk)
    for column, value in where.items():
        query = query.where(getattr(CodeChunk, column) == value)
    return (await db.execute(query)).scalar_one()


async def _chunks_of(db: AsyncSession, source_file_id: int) -> list[CodeChunk]:
    rows = await db.execute(
        select(CodeChunk)
        .where(CodeChunk.source_file_id == source_file_id)
        .order_by(CodeChunk.chunk_index)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars())


async def test_parser_output_round_trips_through_the_table(real_db_session: AsyncSession) -> None:
    db = real_db_session
    file_id = await _source_file(db, await _repository(db))
    parsed = code_parser.chunk_source(SOURCE, "python", chunk_max_bytes=90)
    assert parsed.parser == "ast" and len(parsed.chunks) > 2
    db.add_all(_chunk_row(file_id, i, c) for i, c in enumerate(parsed.chunks))
    await db.commit()

    stored = await _chunks_of(db, file_id)

    assert [s.chunk_index for s in stored] == list(range(len(parsed.chunks)))
    for row, chunk in zip(stored, parsed.chunks):
        assert (row.language, row.kind, row.symbol_name, row.symbols, row.parser_mode) == (
            chunk.language, chunk.kind, chunk.symbol, list(chunk.symbols), "ast",
        )
        assert (row.start_line, row.end_line, row.start_byte, row.end_byte) == (
            chunk.start_line, chunk.end_line, chunk.start_byte, chunk.end_byte,
        )
        assert (row.starts_mid_line, row.ends_mid_line) == (chunk.starts_mid_line, chunk.ends_mid_line)
        assert row.content == chunk.content
        assert row.content_hash == hashlib.sha256(chunk.content.encode()).hexdigest()
        assert row.created_at is not None
    # offsets index the parent file's UTF-8 content, and the chunks together are the whole file
    data = SOURCE.encode()
    assert all(data[r.start_byte : r.end_byte].decode() == r.content for r in stored)
    assert "".join(r.content for r in stored) == SOURCE


async def test_fallback_chunks_fit_the_same_table(real_db_session: AsyncSession) -> None:
    db = real_db_session
    file_id = await _source_file(db, await _repository(db), path="main.cob")
    parsed = code_parser.chunk_source(SOURCE, "cobol", chunk_max_bytes=40)
    assert parsed.parser == "fallback"
    db.add_all(_chunk_row(file_id, i, c) for i, c in enumerate(parsed.chunks))
    await db.commit()

    stored = await _chunks_of(db, file_id)

    assert {r.parser_mode for r in stored} == {"fallback"} and {r.kind for r in stored} == {"text"}
    assert {r.language for r in stored} == {"cobol"}  # the fallback keeps the language label it was given
    assert all(r.symbol_name is None and r.symbols == [] for r in stored)
    assert [(r.starts_mid_line, r.ends_mid_line) for r in stored] == [
        (c.starts_mid_line, c.ends_mid_line) for c in parsed.chunks
    ]
    assert "".join(r.content for r in stored) == SOURCE


async def test_defaults_for_optional_columns(real_db_session: AsyncSession) -> None:
    db = real_db_session
    file_id = await _source_file(db, await _repository(db))
    bare = CodeChunk(
        source_file_id=file_id, chunk_index=0, language="python", kind="code", parser_mode="ast", start_line=1, end_line=1,
        start_byte=0, end_byte=3, content="x=1", content_hash="d" * 64,
    )
    db.add(bare)
    await db.commit()

    (stored,) = await _chunks_of(db, file_id)

    assert stored.symbol_name is None and stored.symbols == []
    assert (stored.starts_mid_line, stored.ends_mid_line) == (False, False)


async def test_one_source_file_has_many_chunks_in_index_order(real_db_session: AsyncSession) -> None:
    db = real_db_session
    file_id = await _source_file(db, await _repository(db))
    db.add_all([_chunk(file_id, 2), _chunk(file_id, 0), _chunk(file_id, 1)])
    await db.commit()

    source_file = (
        await db.execute(select(SourceFile).where(SourceFile.id == file_id).execution_options(populate_existing=True))
    ).scalar_one()
    await db.refresh(source_file, attribute_names=["chunks"])

    assert [c.chunk_index for c in source_file.chunks] == [0, 1, 2]  # relationship is ordered
    assert all(c.source_file is source_file for c in source_file.chunks)


async def test_chunk_index_is_unique_per_file_but_not_across_files(real_db_session: AsyncSession) -> None:
    db = real_db_session
    repo_id = await _repository(db)
    first, second = await _source_file(db, repo_id, "a.py"), await _source_file(db, repo_id, "b.py")
    db.add_all([_chunk(first, 0), _chunk(second, 0)])  # same index in different files is fine
    await db.commit()

    db.add(_chunk(first, 0))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


@pytest.mark.parametrize(
    "bad",
    [
        {"parser_mode": "regex"}, {"parser_mode": "AST"}, {"chunk_index": -1},
        {"start_line": 0}, {"start_line": 5, "end_line": 4}, {"start_byte": -1}, {"end_byte": 0},
        {"start_byte": 7, "end_byte": 7}, {"content": None}, {"content_hash": None}, {"kind": None},
    ],
)
async def test_invalid_rows_are_rejected_by_the_database(real_db_session: AsyncSession, bad: dict[str, object]) -> None:
    db = real_db_session
    file_id = await _source_file(db, await _repository(db))
    db.add(_chunk(file_id, **bad))

    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


async def test_a_chunk_needs_an_existing_source_file(real_db_session: AsyncSession) -> None:
    real_db_session.add(_chunk(999_999_999))

    with pytest.raises(IntegrityError):
        await real_db_session.commit()
    await real_db_session.rollback()


async def test_deleting_a_source_file_deletes_its_chunks_only(real_db_session: AsyncSession) -> None:
    db = real_db_session
    repo_id = await _repository(db)
    doomed, kept = await _source_file(db, repo_id, "a.py"), await _source_file(db, repo_id, "b.py")
    db.add_all([_chunk(doomed, 0), _chunk(doomed, 1), _chunk(kept, 0)])
    await db.commit()

    await db.execute(delete(SourceFile).where(SourceFile.id == doomed))  # a Core delete, as persistence does
    await db.commit()

    assert await _count(db, source_file_id=doomed) == 0
    assert await _count(db, source_file_id=kept) == 1


async def test_orm_delete_of_a_source_file_cascades_in_the_database(real_db_session: AsyncSession) -> None:
    db = real_db_session
    file_id = await _source_file(db, await _repository(db))
    db.add_all([_chunk(file_id, 0), _chunk(file_id, 1)])
    await db.commit()
    source_file = (await db.execute(select(SourceFile).where(SourceFile.id == file_id))).scalar_one()

    await db.delete(source_file)  # passive_deletes: the database cascades, no chunks are loaded
    await db.commit()

    assert await _count(db, source_file_id=file_id) == 0


async def test_deleting_a_repository_cascades_through_files_to_chunks(real_db_session: AsyncSession) -> None:
    db = real_db_session
    repo_id = await _repository(db)
    file_id = await _source_file(db, repo_id)
    db.add_all([_chunk(file_id, 0), _chunk(file_id, 1)])
    await db.commit()

    await db.execute(delete(Repository).where(Repository.id == repo_id))
    await db.commit()

    assert await _count(db, source_file_id=file_id) == 0


async def test_snapshot_replacement_removes_the_previous_chunks(real_db_session: AsyncSession) -> None:
    """`persist_source_snapshot` is unchanged; the foreign key cascade keeps chunks in step with files."""
    db = real_db_session
    repo_id = await _repository(db)

    def result(sha: str, files: dict[str, str]) -> ArchiveReadResult:
        entries = tuple(
            ArchiveFile(path=p, language="python", category=Category.SOURCE, content=c,
                        sha256=hashlib.sha256(c.encode()).hexdigest(), size_bytes=len(c.encode()),
                        line_count=c.count("\n"))
            for p, c in files.items()
        )
        return ArchiveReadResult(sha, entries, (), {}, False, None, 0)

    await persist_source_snapshot(db, repo_id, result("1" * 40, {"old.py": "a = 1\n", "keep.py": "b = 2\n"}))
    old_ids = (await db.execute(select(SourceFile.id).where(SourceFile.repository_id == repo_id))).scalars().all()
    db.add_all([_chunk(i, 0) for i in old_ids])
    await db.commit()
    assert await _count(db) >= 2

    await persist_source_snapshot(db, repo_id, result("2" * 40, {"new.py": "c = 3\n"}))

    for old_id in old_ids:
        assert await _count(db, source_file_id=old_id) == 0
    new_ids = (await db.execute(select(SourceFile.id).where(SourceFile.repository_id == repo_id))).scalars().all()
    assert len(new_ids) == 1 and set(new_ids).isdisjoint(old_ids)
    assert await _count(db, source_file_id=new_ids[0]) == 0  # new files start without chunks


# --- table definition ---------------------------------------------------------------------------


def test_table_definition_matches_the_reviewed_design() -> None:
    table = Base.metadata.tables["code_chunks"]

    assert set(table.columns.keys()) == {
        "id", "source_file_id", "chunk_index", "language", "kind", "symbol_name", "symbols", "parser_mode",
        "start_line", "end_line", "start_byte", "end_byte", "starts_mid_line", "ends_mid_line",
        "content", "content_hash", "created_at",
    }
    # the repository and commit are reached through source_files, never duplicated here
    assert not {"repository_id", "commit_sha", "path"} & set(table.columns.keys())
    assert isinstance(table.c.symbols.type, JSONB)
    assert [c.name for c in table.c if not c.nullable and c.name not in {"id"}] == [
        "source_file_id", "chunk_index", "language", "kind", "symbols", "parser_mode", "start_line", "end_line",
        "start_byte", "end_byte", "starts_mid_line", "ends_mid_line", "content", "content_hash", "created_at",
    ]  # only symbol_name is optional

    foreign_keys = [c for c in table.constraints if isinstance(c, ForeignKeyConstraint)]
    assert [(fk.elements[0].target_fullname, fk.ondelete) for fk in foreign_keys] == [("source_files.id", "CASCADE")]
    uniques = {c.name: [col.name for col in c.columns] for c in table.constraints if isinstance(c, UniqueConstraint)}
    assert uniques == {"uq_code_chunks_source_file_chunk_index": ["source_file_id", "chunk_index"]}
    checks = {c.name for c in table.constraints if isinstance(c, CheckConstraint)}
    assert checks == {
        "valid_code_chunk_parser_mode", "valid_code_chunk_index", "valid_code_chunk_lines", "valid_code_chunk_bytes",
    }
    # the unique constraint already indexes (source_file_id, chunk_index); symbol lookup is extra
    assert {i.name: [c.name for c in i.columns] for i in table.indexes} == {
        "ix_code_chunks_symbol_name": ["symbol_name"],
    }


def test_existing_source_file_table_is_unchanged() -> None:
    table = Base.metadata.tables["source_files"]

    assert set(table.columns.keys()) == {
        "id", "repository_id", "commit_sha", "path", "language", "category", "content", "content_hash",
        "size_bytes", "line_count", "created_at",
    }
    assert {c.name for c in table.constraints if isinstance(c, UniqueConstraint)} == {
        "uq_source_files_repository_commit_path"
    }
