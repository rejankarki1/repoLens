"""Saves an `ArchiveReadResult` into PostgreSQL as the repository's latest source-file snapshot.

Storage only: it does not download archives, read ZIPs, parse code, or know about HTTP. The caller
supplies a repository ID, an already-read result and, optionally, the code chunks of its files.
"""

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from sqlalchemy import bindparam, delete, func, insert, literal_column, select
from sqlalchemy.dialects.postgresql import to_tsvector
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.models.entities import CodeChunk, Repository, SourceFile
from apps.api.services.archive_reader import ArchiveReadResult
from apps.api.services.code_parser import CodeChunk as ParsedChunk
from apps.api.services.code_search import search_documents

# The weighted full-text document of a chunk, built by PostgreSQL from the two normalized texts in each row.
SEARCH_VECTOR = func.setweight(to_tsvector("simple", bindparam("meta_text")), literal_column("'A'")).op("||")(
    func.setweight(to_tsvector("simple", bindparam("body_text")), literal_column("'C'"))
)


class SourcePersistenceError(Exception):
    """Base class for errors raised by this module."""


class UnknownRepositoryError(SourcePersistenceError):
    """No `repositories` row exists for the given ID."""


class IncompleteSnapshotError(SourcePersistenceError):
    """The archive result was truncated, so it is not a complete picture of the repository."""


def chunk_row(source_file_id: int, chunk_index: int, chunk: ParsedChunk, path: str = "") -> dict[str, object]:
    """The `code_chunks` row for a parser chunk (values are bounded to their column sizes).

    `meta_text` / `body_text` are not columns: the INSERT turns them into `search_vector`.
    """
    meta_text, body_text = search_documents(path, chunk.symbols, chunk.content)
    return {
        "source_file_id": source_file_id,
        "chunk_index": chunk_index,
        "language": chunk.language[:64],
        "kind": chunk.kind[:32],
        "symbol_name": chunk.symbol[:255] if chunk.symbol else None,
        "symbols": list(chunk.symbols),
        "parser_mode": chunk.parser,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "start_byte": chunk.start_byte,
        "end_byte": chunk.end_byte,
        "starts_mid_line": chunk.starts_mid_line,
        "ends_mid_line": chunk.ends_mid_line,
        "content": chunk.content,
        "content_hash": hashlib.sha256(chunk.content.encode("utf-8")).hexdigest(),
        "meta_text": meta_text,
        "body_text": body_text,
    }


async def persist_source_snapshot(
    db: AsyncSession,
    repository_id: int,
    result: ArchiveReadResult,
    chunks_by_path: Mapping[str, Sequence[ParsedChunk]] | None = None,
) -> int:
    """Replace the repository's stored snapshot with `result` and return the number of files saved.

    `chunks_by_path` maps a file's path to its code chunks (in order); files without an entry simply
    have no chunks. Files and chunks are written in the same transaction, so they are always
    consistent: a chunk can only exist next to the file it was cut from, in the current snapshot.

    Everything happens in one transaction that this function commits. If anything fails, the
    transaction is rolled back and the previously stored snapshot, its chunks and its ingestion
    metadata are left exactly as they were. Truncated results are rejected before the database is
    touched.

    The repository row is locked (`SELECT ... FOR UPDATE`) for the duration, so concurrent
    ingestions of the same repository run one after another; the last one to commit wins.
    Old rows are deleted before the new ones are inserted (the foreign key cascade removes the old
    chunks), so re-ingesting the same commit cannot create duplicates, no rows from a previous commit
    survive, and a snapshot stored before chunks existed gets its chunks on the next ingestion.
    """
    if result.truncated:
        raise IncompleteSnapshotError(
            f"Archive for {result.commit_sha} was truncated ({result.limit_hit}); "
            "refusing to store a partial snapshot."
        )

    chunks_by_path = chunks_by_path or {}
    unknown_paths = set(chunks_by_path) - {entry.path for entry in result.entries}
    if unknown_paths:
        raise ValueError(f"Chunks given for files that are not in the snapshot: {sorted(unknown_paths)[:3]}")

    try:
        repository = (
            await db.execute(
                select(Repository)
                .where(Repository.id == repository_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if repository is None:
            raise UnknownRepositoryError(f"Repository {repository_id} does not exist.")

        await db.execute(delete(SourceFile).where(SourceFile.repository_id == repository_id))
        if result.entries:
            inserted = await db.execute(
                insert(SourceFile).returning(SourceFile.id, SourceFile.path),
                [
                    {
                        "repository_id": repository_id,
                        "commit_sha": result.commit_sha,
                        "path": entry.path,
                        "language": entry.language,
                        "category": entry.category.value,
                        "content": entry.content,
                        "content_hash": entry.sha256,
                        "size_bytes": entry.size_bytes,
                        "line_count": entry.line_count,
                    }
                    for entry in result.entries
                ],
            )
            file_ids = {path: file_id for file_id, path in inserted}
            rows = [
                chunk_row(file_ids[path], index, chunk, path)
                for path, chunks in chunks_by_path.items()
                for index, chunk in enumerate(chunks)
            ]
            if rows:
                await db.execute(insert(CodeChunk).values(search_vector=SEARCH_VECTOR), rows)  # batched

        repository.last_ingested_commit_sha = result.commit_sha
        repository.ingestion_status = "completed"
        repository.ingestion_truncated = False
        repository.ingestion_error = None
        repository.last_ingested_at = datetime.now(timezone.utc)
        repository.ingestion_heartbeat_at = None
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    return len(result.entries)
