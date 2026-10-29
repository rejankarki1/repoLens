"""Turns the files of a repository snapshot into code chunks (CPU-bound, no database access).

For every file the existing parser adapter picks a tree-sitter grammar from the file extension and
returns syntax-aware chunks; unsupported languages, missing grammars and parser failures get the
adapter's line-based fallback, so every non-empty file is chunked and no text is lost. One bad file
never aborts the run. Grammars are never downloaded here (see `code_parser`).

Synchronous: call it with `asyncio.to_thread` so the ingestion job's event loop (and its heartbeat)
keeps running.
"""

import logging
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from apps.api.services.archive_reader import SourceFile as ArchiveFile
from apps.api.services.code_parser import CodeChunk, chunk_source, language_for_path

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IndexStats:
    files: int
    chunks: int
    ast_files: int
    fallback_files: int
    failed_files: int  # files that could not be chunked at all (never expected)
    fallback_reasons: dict[str, int]  # e.g. {"unsupported_language": 40, "grammar_missing": 0}
    duration_ms: float


@dataclass(frozen=True, slots=True)
class CodeIndex:
    chunks_by_path: dict[str, tuple[CodeChunk, ...]]
    stats: IndexStats


def index_entries(entries: Iterable[ArchiveFile], *, chunk_max_bytes: int | None = None) -> CodeIndex:
    """Chunk every file. `chunk_max_bytes` defaults to `Settings.code_chunk_max_bytes`."""
    started = time.perf_counter()
    chunks_by_path: dict[str, tuple[CodeChunk, ...]] = {}
    reasons: Counter[str] = Counter()
    files = ast_files = fallback_files = failed_files = 0

    for entry in entries:
        files += 1
        # A supported extension selects its grammar; anything else is labelled with the archive
        # reader's language (e.g. "go", "dockerfile", "unknown") and chunked by the line fallback.
        language = language_for_path(entry.path) or entry.language
        try:
            chunked = chunk_source(entry.content, language, chunk_max_bytes=chunk_max_bytes)
        except Exception:
            logger.warning("Chunking %s failed; retrying as plain text", entry.path, exc_info=True)
            try:
                chunked = chunk_source(entry.content, None, chunk_max_bytes=chunk_max_bytes)
            except Exception:
                logger.exception("Could not chunk %s; it is stored without chunks", entry.path)
                failed_files += 1
                continue
        if chunked.parser == "ast":
            ast_files += 1
        else:
            fallback_files += 1
            reasons[chunked.fallback_reason or "unknown"] += 1
        if chunked.chunks:
            chunks_by_path[entry.path] = chunked.chunks

    stats = IndexStats(
        files=files,
        chunks=sum(len(chunks) for chunks in chunks_by_path.values()),
        ast_files=ast_files,
        fallback_files=fallback_files,
        failed_files=failed_files,
        fallback_reasons=dict(reasons),
        duration_ms=(time.perf_counter() - started) * 1000,
    )
    if reasons.get("grammar_missing"):
        logger.warning(
            "%s file(s) were chunked as plain text because a tree-sitter grammar is not installed; "
            "run ensure_grammars() at deploy time",
            reasons["grammar_missing"],
        )
    return CodeIndex(chunks_by_path, stats)
