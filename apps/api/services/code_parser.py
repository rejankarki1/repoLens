"""Thin adapter over tree-sitter-language-pack: parsing, and normalized, loss-free code chunks.

It leans on the library's `process()` (structure, imports, exports, diagnostics and syntax-aware
chunking) and adds only what RepoLens needs on top:

* **One chunk shape** (`CodeChunk`) for every language and for the fallback. A chunk is identified by
  its exact UTF-8 **byte range**; line numbers are derived from those offsets (1-based, inclusive) and
  are not unique identifiers, because an oversized definition can be cut inside a line.
* **A line-based fallback** (`chunk_source`) for unsupported languages, missing grammars, parser
  failures and inconsistent parser output, so source text is never silently dropped.
* **Guards.** `process()` silently downloads a grammar the first time an uncached language is
  requested (13 MB for COBOL; blocking, needs the network). Only allowlisted, already-downloaded
  languages ever reach it; anything else is chunked by the fallback. Downloads happen only through
  the explicit `ensure_grammars()`, at build or startup, never while processing a repository.

Everything is synchronous and CPU-bound (the native call releases the GIL). Async callers, such as
the RQ ingestion job, **must** run it with `asyncio.to_thread`: called directly it blocks the event
loop for ~0.8 s on a 1 MB file (delaying the job's heartbeat task), while in a thread the loop is
never stalled for more than ~25 ms. It is safe to call from several threads at once and from freshly
spawned or forked worker processes (verified; results are identical).

The library's `parse_timeout_ms` is deliberately **not used**: after a `ParseTimeoutError` the next
parse of the same language can return garbage (or panic), because the cancelled parse is resumed.
Work is bounded by `max_source_bytes` instead (parsing is roughly linear, ~0.2 s for 1 MB), and the
RQ job timeout is the hard backstop.
"""

from __future__ import annotations

import logging
import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field

import tree_sitter_language_pack as tslp

from apps.api.config import get_settings

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = ("python", "javascript", "typescript", "tsx")

# Defaults are prototype values to revisit in Phase 4.2 (they depend on the embedding model).
DEFAULT_CHUNK_MAX_BYTES = 1_500
DEFAULT_MAX_SOURCE_BYTES = 1_048_576  # same cap as `archive_reader_max_file_bytes`
# Smallest allowed chunk size: guarantees a UTF-8 character (up to 4 bytes) always fits in a chunk.
MIN_CHUNK_BYTES = 16

PARSER_AST = "ast"
PARSER_FALLBACK = "fallback"
TEXT_LANGUAGE = "text"

_NEWLINE = re.compile(rb"\n")


class CodeParserError(Exception):
    """Base class for errors raised by this module."""


class UnsupportedLanguageError(CodeParserError):
    """The language is not one of `SUPPORTED_LANGUAGES`."""


class GrammarNotInstalledError(CodeParserError):
    """A supported language's grammar is not in the local cache; see `ensure_grammars()`."""


class InvalidChunksError(CodeParserError):
    """The parser's chunks do not tile the source exactly (gap, overlap, or invalid UTF-8)."""


@dataclass(frozen=True, slots=True)
class LineRange:
    """1-based, inclusive on both ends."""

    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Symbol:
    kind: str  # "function", "class", "method", "interface", "enum", "namespace", ...
    name: str | None  # None for anonymous functions
    signature: str | None
    lines: LineRange
    children: tuple[Symbol, ...] = ()
    start_byte: int = 0


@dataclass(frozen=True, slots=True)
class ParsedImport:
    source: str  # the import statement text (not the module specifier)
    items: tuple[str, ...]
    line: int


@dataclass(frozen=True, slots=True)
class ParsedExport:
    name: str
    kind: str  # "named", "default", "reexport"
    line: int


@dataclass(frozen=True, slots=True)
class ParseDiagnostic:
    message: str
    severity: str
    line: int  # 1-based
    byte_column: int  # 0-based, in UTF-8 bytes (not characters)


@dataclass(frozen=True, slots=True)
class CodeChunk:
    """A contiguous slice of the source, normalized across languages and parser modes.

    `source[start_byte:end_byte]` (as UTF-8 bytes) is `content`, exactly. Lines are 1-based and
    inclusive, derived from the offsets: `start_line` is the line containing the first byte and
    `end_line` the line containing the last byte (a trailing newline belongs to the line it ends).
    When `starts_mid_line`/`ends_mid_line` is set, the chunk begins/ends inside a line, so its line
    range alone does not identify its content: use the byte range.
    """

    language: str
    kind: str  # "class", "function", "method", ... of the primary symbol; "imports"; "code"; "text" (fallback)
    symbol: str | None  # the primary (first) symbol in the chunk, or the definition a split chunk belongs to
    symbols: tuple[str, ...]  # every symbol that starts in the chunk (all depths), in source order
    start_line: int
    end_line: int
    start_byte: int  # inclusive, UTF-8 offset into the source
    end_byte: int  # exclusive
    content: str
    parser: str  # PARSER_AST or PARSER_FALLBACK
    starts_mid_line: bool
    ends_mid_line: bool


@dataclass(frozen=True, slots=True)
class ParsedFile:
    language: str
    total_lines: int
    error_count: int
    symbols: tuple[Symbol, ...] = ()
    imports: tuple[ParsedImport, ...] = ()
    exports: tuple[ParsedExport, ...] = ()
    diagnostics: tuple[ParseDiagnostic, ...] = ()
    chunks: tuple[CodeChunk, ...] = field(default=())

    @property
    def ok(self) -> bool:
        """False when the source contains syntax errors (results are then best-effort)."""
        return self.error_count == 0


@dataclass(frozen=True, slots=True)
class ChunkedSource:
    """Result of `chunk_source`: chunks that always tile the whole source, plus how they were made."""

    language: str
    parser: str  # PARSER_AST or PARSER_FALLBACK
    fallback_reason: str | None  # unsupported_language | grammar_missing | parse_failed | invalid_chunks
    chunks: tuple[CodeChunk, ...]
    syntax_errors: int = 0  # tree-sitter error nodes (AST mode only); chunks are still complete


def language_for_path(path: str) -> str | None:
    """The supported language for a file path, or None (also for languages we do not parse yet)."""
    language = tslp.detect_language_from_path(path)
    return language if language in SUPPORTED_LANGUAGES else None


def missing_grammars() -> list[str]:
    """Supported languages whose grammar is not in the local cache."""
    cached = set(tslp.downloaded_languages())
    return [language for language in SUPPORTED_LANGUAGES if language not in cached]


def ensure_grammars() -> list[str]:
    """Download any missing supported grammars (network). Call at build/startup, never while parsing."""
    missing = missing_grammars()
    if missing:
        tslp.download(missing)
    return missing


def parse_source(
    source: str,
    language: str,
    *,
    chunk_max_bytes: int | None = DEFAULT_CHUNK_MAX_BYTES,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
) -> ParsedFile:
    """Parse `source` and return its structure, imports, exports, diagnostics and chunks.

    Syntax errors do not raise: the result has `ok == False`, diagnostics, and whatever structure
    tree-sitter could recover. `chunk_max_bytes=None` skips chunking. Raises `UnsupportedLanguageError`,
    `GrammarNotInstalledError`, or `CodeParserError` (source too large, library failure).
    """
    data = _utf8(source)
    if language not in SUPPORTED_LANGUAGES:
        raise UnsupportedLanguageError(f"Unsupported language {language!r}; supported: {SUPPORTED_LANGUAGES}")
    if chunk_max_bytes is not None and chunk_max_bytes < MIN_CHUNK_BYTES:
        raise ValueError(f"chunk_max_bytes must be at least {MIN_CHUNK_BYTES}, or None to disable chunking")
    if language not in tslp.downloaded_languages():
        # Calling process() here would start an implicit, blocking network download.
        raise GrammarNotInstalledError(f"Grammar for {language!r} is not installed; run ensure_grammars()")

    config = tslp.ProcessConfig(
        language=language,
        structure=True,
        imports=True,
        exports=True,
        symbols=True,
        diagnostics=True,
        chunk_max_size=chunk_max_bytes,
        max_source_bytes=max_source_bytes,
    )
    try:
        result = tslp.process(source, config)
    except tslp.Error as exc:  # InvalidRangeError (source too large), ParseFailedError, ...
        raise CodeParserError(f"Could not parse {language} source: {exc}") from exc

    symbols = tuple(_symbol(item) for item in result.structure)
    return ParsedFile(
        language=result.language,
        total_lines=result.metrics.total_lines if result.metrics else 0,
        error_count=result.metrics.error_count if result.metrics else 0,
        symbols=symbols,
        imports=tuple(
            ParsedImport(item.source, tuple(item.items), item.span.start_line + 1 if item.span else 0)
            for item in result.imports
        ),
        exports=tuple(
            ParsedExport(item.name, str(item.kind).lower(), item.span.start_line + 1 if item.span else 0)
            for item in result.exports
        ),
        diagnostics=tuple(
            ParseDiagnostic(item.message, str(item.severity).lower(), item.span.start_line + 1, item.span.start_column)
            for item in result.diagnostics
            if item.span
        ),
        chunks=(
            _ast_chunks(result.chunks, data, language, symbols, _definitions(symbols, result.symbols))
            if chunk_max_bytes is not None
            else ()
        ),
    )


def chunk_source(
    source: str,
    language: str | None,
    *,
    chunk_max_bytes: int | None = None,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
) -> ChunkedSource:
    """Split `source` into bounded chunks that together are exactly the source. Never raises on bad input.

    Uses the library's syntax-aware chunker when the language is supported and its grammar is
    installed, otherwise (or if the parser fails or returns inconsistent chunks) a line-based fallback.
    Syntax errors alone do not trigger the fallback: tree-sitter still chunks the whole file.
    `chunk_max_bytes` defaults to `Settings.code_chunk_max_bytes` (1,500). Only invalid *arguments* raise
    (`ValueError`): a chunk size under `MIN_CHUNK_BYTES`, or text that is not valid Unicode.
    """
    _utf8(source)
    if chunk_max_bytes is None:
        chunk_max_bytes = get_settings().code_chunk_max_bytes
    if chunk_max_bytes < MIN_CHUNK_BYTES:
        raise ValueError(f"chunk_max_bytes must be at least {MIN_CHUNK_BYTES}")
    name = (language or "").strip() or TEXT_LANGUAGE

    reason: str
    try:
        parsed = parse_source(source, name, chunk_max_bytes=chunk_max_bytes, max_source_bytes=max_source_bytes)
        return ChunkedSource(parsed.language, PARSER_AST, None, parsed.chunks, parsed.error_count)
    except UnsupportedLanguageError:
        reason = "unsupported_language"
    except GrammarNotInstalledError:
        reason = "grammar_missing"
    except InvalidChunksError:
        reason = "invalid_chunks"
        logger.warning("Parser chunks for %s were inconsistent; using the line fallback", name)
    except CodeParserError as exc:  # expected library failures, e.g. a source over `max_source_bytes`
        reason = "parse_failed"
        logger.warning("Parsing %s failed (%s); using the line fallback", name, exc)
    except Exception:  # anything unexpected from the library
        reason = "parse_failed"
        logger.warning("Parsing %s failed unexpectedly; using the line fallback", name, exc_info=True)
    except BaseException as exc:
        # A Rust panic inside the native library reaches Python as pyo3's PanicException, which is a
        # BaseException rather than an Exception. Treat it as a parser failure; re-raise anything else.
        if type(exc).__name__ != "PanicException":
            raise
        reason = "parse_failed"
        logger.warning("The native parser panicked on %s; using the line fallback", name, exc_info=True)

    return ChunkedSource(name, PARSER_FALLBACK, reason, _fallback_chunks(source, name, chunk_max_bytes))


def _utf8(source: str) -> bytes:
    """The source as UTF-8, or ValueError for text that cannot be (a lone surrogate): exact, loss-free
    chunking is impossible for it, and PostgreSQL text could not store it either."""
    try:
        return source.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("source is not valid Unicode text: it contains a lone surrogate") from exc


class _Lines:
    """Maps UTF-8 byte offsets to 1-based line numbers (only `\n` ends a line; `\r\n` is fine)."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self._ends = [match.end() for match in _NEWLINE.finditer(data)]  # offset just after each newline

    def row(self, offset: int) -> int:
        """0-based row of the byte at `offset`."""
        return bisect_right(self._ends, offset)

    def spans(self) -> list[tuple[int, int]]:
        """(start, end) of every line including its newline; the last line may lack one."""
        starts = [0, *self._ends]
        ends = [*self._ends, len(self.data)]
        return [(a, b) for a, b in zip(starts, ends) if b > a]

    def chunk(
        self,
        start: int,
        end: int,
        *,
        language: str,
        kind: str,
        symbol: str | None,
        parser: str,
        symbols: tuple[str, ...] = (),
    ) -> CodeChunk:
        try:
            content = self.data[start:end].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidChunksError(f"Chunk bytes {start}-{end} are not valid UTF-8") from exc
        return CodeChunk(
            language=language,
            kind=kind,
            symbol=symbol,
            symbols=symbols,
            start_line=self.row(start) + 1,
            end_line=self.row(end - 1) + 1,
            start_byte=start,
            end_byte=end,
            content=content,
            parser=parser,
            starts_mid_line=start > 0 and self.data[start - 1] != 0x0A,
            ends_mid_line=end < len(self.data) and self.data[end - 1] != 0x0A,
        )


def _definitions(symbols: tuple[Symbol, ...], infos: list[tslp.SymbolInfo]) -> list[tuple[int, str]]:
    """(start_byte, name) of every named symbol: the structure at all depths plus the library's symbol
    list, which also covers things the structure omits (type aliases, constants)."""
    found: list[tuple[int, str]] = []

    def walk(items: tuple[Symbol, ...]) -> None:
        for item in items:
            if item.name:
                found.append((item.start_byte, item.name))
            walk(item.children)

    walk(symbols)
    found.extend((info.span.start_byte, info.name) for info in infos if info.name and info.span)
    found.sort(key=lambda entry: entry[0])  # stable: structure entries stay ahead of equal positions
    return found


def _ast_chunks(
    items: list[tslp.CodeChunk],
    data: bytes,
    language: str,
    symbols: tuple[Symbol, ...],
    definitions: list[tuple[int, str]] = (),  # type: ignore[assignment]
) -> tuple[CodeChunk, ...]:
    """Normalize the library's chunks and check that they tile the source exactly."""
    lines = _Lines(data)
    starts = [start for start, _ in definitions]
    built: list[CodeChunk] = []
    for item in items:
        primary = _primary(item, lines, symbols)
        inside = dict.fromkeys(
            name for _, name in definitions[bisect_left(starts, item.start_byte) : bisect_left(starts, item.end_byte)]
        )
        if primary["symbol"] is not None and primary["symbol"] not in inside:
            inside = {primary["symbol"]: None, **inside}  # a piece of a definition that started earlier
        built.append(
            lines.chunk(
                item.start_byte,
                item.end_byte,
                language=language,
                parser=PARSER_AST,
                symbols=tuple(inside),
                **primary,
            )
        )
    chunks = tuple(built)
    position = 0
    for chunk in chunks:
        if chunk.start_byte != position or chunk.end_byte <= chunk.start_byte:
            raise InvalidChunksError("Parser chunks are not contiguous")
        position = chunk.end_byte
    if position != len(data):
        raise InvalidChunksError("Parser chunks do not cover the whole source")
    return chunks


def _primary(item: tslp.CodeChunk, lines: _Lines, symbols: tuple[Symbol, ...]) -> dict[str, object]:
    """`kind` and `symbol` of a chunk: its primary (outermost-first, named) symbol, from the structure.

    A chunk that starts inside a definition (a split class or function) belongs to the innermost named
    symbol containing its first line. Otherwise it is the first top-level symbol that starts in the
    chunk (so imports followed by a class are "class"). With neither, it is "imports" or "code".
    """
    first = lines.row(item.start_byte) + 1
    last = lines.row(max(item.end_byte - 1, item.start_byte)) + 1
    found = _containing(symbols, first) or next((s for s in symbols if first <= s.lines.start <= last), None)
    if found is not None:
        return {"kind": found.kind, "symbol": found.name}
    node_types = item.metadata.node_types if item.metadata is not None else []
    return {"kind": "imports" if node_types and all("import" in t for t in node_types) else "code", "symbol": None}


def _containing(symbols: tuple[Symbol, ...], line: int) -> Symbol | None:
    """Innermost named symbol whose lines include `line`."""
    for symbol in symbols:
        if symbol.lines.start <= line <= symbol.lines.end:
            return _containing(symbol.children, line) or (symbol if symbol.name else None)
    return None


def _fallback_chunks(source: str, language: str, max_bytes: int) -> tuple[CodeChunk, ...]:
    """Line-based chunks of at most `max_bytes` bytes that tile the source exactly.

    Whole lines are grouped; a line longer than the limit is cut at character boundaries (never
    inside a UTF-8 sequence or between `\r` and `\n`). Nothing is dropped, so the chunks'
    contents concatenate back to the source.
    """
    data = source.encode("utf-8")
    lines = _Lines(data)
    cuts: list[tuple[int, int]] = []
    start = 0  # start of the chunk being built
    for line_start, line_end in lines.spans():
        if line_end - line_start > max_bytes:  # one over-long line: flush, then slice it
            if line_start > start:
                cuts.append((start, line_start))
            position = line_start
            while position < line_end:
                end = _safe_end(data, position, min(position + max_bytes, line_end))
                cuts.append((position, end))
                position = end
            start = line_end
        elif line_end - start > max_bytes:  # adding this line would overflow the chunk
            cuts.append((start, line_start))
            start = line_start
    if start < len(data):
        cuts.append((start, len(data)))
    return tuple(
        lines.chunk(a, b, language=language, kind="text", symbol=None, parser=PARSER_FALLBACK) for a, b in cuts
    )


def _safe_end(data: bytes, start: int, end: int) -> int:
    """Move `end` back so a cut never falls inside a multi-byte character or between \r and \n."""
    if end >= len(data):
        return end
    while end > start + 1 and (data[end] & 0xC0) == 0x80:  # data[end] is a continuation byte
        end -= 1
    if end > start + 1 and data[end - 1] == 0x0D and data[end] == 0x0A:
        end -= 1
    return end


def _symbol(item: tslp.StructureItem) -> Symbol:
    span = item.span
    return Symbol(
        kind=str(item.kind).lower(),
        name=item.name,
        signature=item.signature,
        lines=LineRange(span.start_line + 1, span.end_line + 1) if span else LineRange(0, 0),
        children=tuple(_symbol(child) for child in item.children),
        start_byte=span.start_byte if span else 0,
    )
