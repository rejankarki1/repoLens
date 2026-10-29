"""Tests for the tree-sitter parsing adapter.

They use the real grammars (no mocks) for Python, JavaScript, TypeScript and TSX. The grammars are
downloaded on first use by tree-sitter-language-pack; the module fixture below fetches any that are
missing and skips the whole module if that is impossible (offline), instead of failing.

The "Known limitations" section pins behaviour of the pinned library version that Phase 4.2 has to
work around; if a library upgrade changes one of them, that test fails and should be reviewed.
"""

from types import SimpleNamespace

import pytest
import tree_sitter_language_pack as tslp

from apps.api.services import code_parser
from apps.api.services.code_parser import (
    ChunkedSource,
    CodeParserError,
    GrammarNotInstalledError,
    InvalidChunksError,
    ParsedFile,
    Symbol,
    UnsupportedLanguageError,
    _ast_chunks,
    chunk_source,
    ensure_grammars,
    language_for_path,
    missing_grammars,
    parse_source,
)


@pytest.fixture(scope="module", autouse=True)
def _grammars_available() -> None:
    if missing_grammars():
        try:
            ensure_grammars()
        except Exception as exc:  # no network / cache not writable
            pytest.skip(f"tree-sitter grammars unavailable: {exc}")


def _names(symbols: tuple[Symbol, ...]) -> list[str | None]:
    return [s.name for s in symbols]


def _find(symbols: tuple[Symbol, ...], name: str) -> Symbol:
    return next(s for s in symbols if s.name == name)


PYTHON = '''import os
from typing import List

class Greeter:
    """Says hi."""
    def __init__(self, name):
        self.name = name

    def hello(self, x: int) -> str:
        return "hi"

async def main(args: List[str]) -> None:
    print("go")
'''

JAVASCRIPT = '''import fs from "fs";
import { a, b } from "./mod";
export const PI = 3.14;
export function add(x, y) { return x + y; }
export default class Calc {
  constructor() {}
  mul(a, b) { return a * b; }
}
const arrow = (n) => n * 2;
'''

TYPESCRIPT = '''import type { Foo } from "./foo";
export interface User { id: number; name: string }
enum Color { Red, Green }
export class Repo<T> implements Store {
  private items: T[] = [];
  async get(id: number): Promise<T | undefined> { return this.items[id]; }
}
export function helper<T>(x: T): T { return x; }
'''

TSX = '''import React, { useState } from "react";
interface Props { label: string }
export default function Button({ label }: Props) {
  const [n, setN] = useState(0);
  return <button onClick={() => setN(n + 1)}>{label}: {n}</button>;
}
export const Card = ({ title }: { title: string }) => <div className="card">{title}</div>;
class Legacy extends React.Component { render() { return <span/>; } }
'''


# --- structure ---------------------------------------------------------------------------------


def test_python_functions_classes_and_imports_with_one_based_lines() -> None:
    parsed = parse_source(PYTHON, "python")

    assert parsed.ok and parsed.language == "python" and parsed.total_lines == 13
    assert [(s.kind, s.name) for s in parsed.symbols] == [("class", "Greeter"), ("function", "main")]
    greeter = _find(parsed.symbols, "Greeter")
    assert (greeter.lines.start, greeter.lines.end) == (4, 10)  # `class Greeter:` is line 4 of the source
    assert [(m.kind, m.name, m.lines.start, m.lines.end) for m in greeter.children] == [
        ("function", "__init__", 6, 7),
        ("function", "hello", 9, 10),
    ]
    assert _find(parsed.symbols, "main").lines.start == 12
    assert _find(parsed.symbols, "main").signature == "async def main(args: List[str]) -> None:"
    assert [(i.source, i.items, i.line) for i in parsed.imports] == [
        ("import os", ("os",), 1),
        ("from typing import List", ("List",), 2),
    ]


def test_javascript_functions_classes_and_exports() -> None:
    parsed = parse_source(JAVASCRIPT, "javascript")

    assert parsed.ok
    assert _names(parsed.symbols) == ["add", "Calc", "arrow"]
    calc = _find(parsed.symbols, "Calc")
    assert [(m.kind, m.name) for m in calc.children] == [("method", "constructor"), ("method", "mul")]
    assert [(e.name, e.kind, e.line) for e in parsed.exports] == [
        ("PI", "named", 3),
        ("add", "named", 4),
        ("Calc", "default", 5),
    ]
    assert [i.line for i in parsed.imports] == [1, 2]


def test_typescript_interfaces_enums_classes_and_functions() -> None:
    parsed = parse_source(TYPESCRIPT, "typescript")

    assert parsed.ok
    assert [(s.kind, s.name) for s in parsed.symbols] == [
        ("interface", "User"),
        ("enum", "Color"),
        ("class", "Repo"),
        ("function", "helper"),
    ]
    repo = _find(parsed.symbols, "Repo")
    assert [m.name for m in repo.children] == ["get"]
    assert repo.signature == "class Repo<T> implements Store"
    assert {e.name for e in parsed.exports} >= {"User", "Repo", "helper"}
    assert parsed.imports[0].items == ("Foo",)


def test_tsx_components() -> None:
    parsed = parse_source(TSX, "tsx")

    assert parsed.ok
    assert _names(parsed.symbols) == ["Props", "Button", "Card", "Legacy"]
    button, card = _find(parsed.symbols, "Button"), _find(parsed.symbols, "Card")
    assert (button.kind, button.lines.start, button.lines.end) == ("function", 3, 6)
    assert card.kind == "function" and card.lines.start == 7  # arrow-function component
    assert [(e.name, e.kind) for e in parsed.exports] == [("Button", "default"), ("Card", "named")]


@pytest.mark.parametrize(
    ("path", "language"),
    [
        ("a.py", "python"), ("a.js", "javascript"), ("a.jsx", "javascript"), ("a.mjs", "javascript"),
        ("a.ts", "typescript"), ("a.d.ts", "typescript"), ("src/App.tsx", "tsx"),
        ("a.json", None), ("README.md", None), ("Dockerfile", None),
    ],
)
def test_language_for_path_only_returns_supported_languages(path: str, language: str | None) -> None:
    assert language_for_path(path) == language


# --- invalid and empty input -------------------------------------------------------------------


def test_python_syntax_error_reports_diagnostics_and_recovers_structure() -> None:
    source = "def f(:\n    return 1\nclass A:\n  def ok(self): pass\nx = (1,\n"

    parsed = parse_source(source, "python")

    assert not parsed.ok and parsed.error_count == 2
    assert [(d.severity, d.line) for d in parsed.diagnostics] == [("error", 1), ("error", 5)]
    assert _names(parsed.symbols) == ["f", "A"]  # tree-sitter recovered both definitions
    assert "".join(chunk.content for chunk in parsed.chunks) == source  # errors never lose text


@pytest.mark.parametrize(
    ("language", "source"),
    [
        ("javascript", "function f( { return 1 }\nconst a = ;\n"),
        ("typescript", "interface X { a: }\nclass B {\n"),
        ("tsx", "const A = () => <div>;\n"),
    ],
)
def test_syntax_errors_in_every_language_do_not_raise(language: str, source: str) -> None:
    parsed = parse_source(source, language)

    assert not parsed.ok
    assert parsed.diagnostics and all(d.line >= 1 for d in parsed.diagnostics)


def test_empty_and_whitespace_only_sources() -> None:
    empty = parse_source("", "python")
    assert (empty.ok, empty.total_lines, empty.symbols, empty.imports, empty.chunks) == (True, 0, (), (), ())

    blank = parse_source("\n\n  \n", "python")
    assert blank.ok and blank.symbols == () and len(blank.chunks) <= 1

    comment_only = parse_source("# just a comment\n", "python")
    assert comment_only.ok and comment_only.symbols == () and len(comment_only.chunks) == 1


# --- chunking ----------------------------------------------------------------------------------

THREE_CLASSES = "import os\n\n" + "\n".join(
    f'class C{i}:\n    """Doc."""\n    def m1(self, x):\n        total = 0\n        return total\n\n'
    f'    def m2(self):\n        return "{"z" * 40}"\n'
    for i in range(3)
)


def _assert_chunks_tile_source(source: str, parsed: ParsedFile, max_bytes: int) -> None:
    data = source.encode("utf-8")
    assert parsed.chunks
    assert parsed.chunks[0].start_byte == 0 and parsed.chunks[-1].end_byte == len(data)
    for before, after in zip(parsed.chunks, parsed.chunks[1:]):
        assert before.end_byte == after.start_byte  # contiguous: no gaps, no overlap
    for chunk in parsed.chunks:
        assert chunk.end_byte - chunk.start_byte <= max_bytes
        assert data[chunk.start_byte : chunk.end_byte].decode("utf-8") == chunk.content
    assert "".join(c.content for c in parsed.chunks) == source


def test_chunks_follow_definition_boundaries_with_one_based_inclusive_lines() -> None:
    parsed = parse_source(THREE_CLASSES, "python", chunk_max_bytes=300)

    _assert_chunks_tile_source(THREE_CLASSES, parsed, 300)
    assert len(parsed.chunks) >= 3
    source_lines = THREE_CLASSES.splitlines(keepends=True)
    for chunk in parsed.chunks:
        assert not chunk.starts_mid_line and not chunk.ends_mid_line
        # start/end are 1-based and inclusive: slicing the source by them reproduces the chunk text
        assert "".join(source_lines[chunk.start_line - 1 : chunk.end_line]) == chunk.content
    assert parsed.chunks[0].start_line == 1
    assert parsed.chunks[-1].end_line == len(source_lines)
    assert [c.symbol for c in parsed.chunks if c.symbol][:3] == ["C0", "C1", "C2"]


def test_chunking_can_be_disabled_and_rejects_non_positive_sizes() -> None:
    assert parse_source(PYTHON, "python", chunk_max_bytes=None).chunks == ()
    for too_small in (0, -1, 15):
        with pytest.raises(ValueError):
            parse_source(PYTHON, "python", chunk_max_bytes=too_small)
        with pytest.raises(ValueError):
            chunk_source(PYTHON, "python", chunk_max_bytes=too_small)


def test_chunk_offsets_are_utf8_bytes_not_characters() -> None:
    source = 'naïve = "héllo wörld"\ndef größe():\n    return "日本語"\n'

    parsed = parse_source(source, "python", chunk_max_bytes=40)

    _assert_chunks_tile_source(source, parsed, 40)
    assert len(source.encode()) > len(source)
    assert "größe" in _names(parsed.symbols)


def test_crlf_line_endings() -> None:
    source = "def a():\r\n    return 1\r\n\r\ndef b():\r\n    return 2\r\n"

    parsed = parse_source(source, "python", chunk_max_bytes=40)

    assert [(s.name, s.lines.start, s.lines.end) for s in parsed.symbols] == [("a", 1, 2), ("b", 4, 5)]
    _assert_chunks_tile_source(source, parsed, 40)


@pytest.mark.parametrize(("language", "source"), [("javascript", JAVASCRIPT), ("typescript", TYPESCRIPT), ("tsx", TSX)])
def test_chunks_tile_the_source_for_every_language(language: str, source: str) -> None:
    _assert_chunks_tile_source(source, parse_source(source, language, chunk_max_bytes=120), 120)


# --- guards ------------------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["cobol", "json", "Python", ""])
def test_unsupported_language_raises_without_calling_the_library(
    language: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("process() must not be reached: it would download a grammar")

    monkeypatch.setattr(tslp, "process", boom)

    with pytest.raises(UnsupportedLanguageError):
        parse_source("x = 1", language)


def test_missing_grammar_raises_instead_of_downloading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tslp, "downloaded_languages", lambda: ["python"])
    monkeypatch.setattr(tslp, "process", lambda *_a, **_k: pytest.fail("process() must not be reached"))

    with pytest.raises(GrammarNotInstalledError):
        parse_source("const a = 1", "tsx")
    assert code_parser.missing_grammars() == ["javascript", "typescript", "tsx"]


def test_oversized_source_surfaces_as_a_parser_error() -> None:
    with pytest.raises(CodeParserError):
        parse_source("x = 1\n" * 100, "python", max_source_bytes=50)


def test_the_library_parse_timeout_is_not_exposed() -> None:
    # `parse_timeout_ms` can leave the library's cached parser in a state that corrupts the next
    # parse of that language, so neither entry point offers it.
    with pytest.raises(TypeError):
        parse_source("x = 1", "python", timeout_ms=1)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        chunk_source("x = 1", "python", timeout_ms=1)  # type: ignore[call-arg]


# --- Known limitations of the pinned library (see Phase 4.1 report) ------------------------------


class TestKnownLimitations:
    def test_python_decorators_are_not_reported_and_spans_exclude_them(self) -> None:
        parsed = parse_source("@decorator(1)\ndef f(x):\n    return x\n", "python")

        assert _find(parsed.symbols, "f").lines.start == 2  # the `@decorator` line (1) is outside the span

    def test_import_aliases_and_default_imports_are_dropped(self) -> None:
        parsed = parse_source('import fs from "fs";\nimport { a, b as c } from "./mod";\n', "javascript")

        assert parsed.imports[0].items == ()  # default import `fs` is missing
        assert parsed.imports[1].items == ("a", "b")  # alias `c` is missing

    def test_typescript_type_aliases_and_plain_constants_are_not_structure(self) -> None:
        parsed = parse_source("export type Id = string | number;\nexport const PI = 3.14;\ninterface I {}\n", "typescript")

        assert _names(parsed.symbols) == ["I"]

    def test_export_alias_is_lost_and_nested_namespace_exports_leak(self) -> None:
        aliased = parse_source("function add(){}\nexport { add as plus };\n", "javascript")
        nested = parse_source("namespace NS { export const x = 1 }\n", "typescript")

        assert [e.name for e in aliased.exports] == ["add"]  # not "plus"
        assert [e.name for e in nested.exports] == ["x"]  # exported from the namespace, not the module

    def test_javascript_syntax_error_can_lose_all_structure(self) -> None:
        parsed = parse_source("function f( { return 1 }\nconst a = ;\n", "javascript")

        assert not parsed.ok
        assert parsed.symbols == ()  # unlike Python, nothing was recovered (it varies with the error)

    def test_oversized_definition_is_cut_inside_a_line(self) -> None:
        source = "def huge():\n" + "".join(f"    v{i} = {i} * 2\n" for i in range(40))

        parsed = parse_source(source, "python", chunk_max_bytes=300)

        _assert_chunks_tile_source(source, parsed, 300)
        later = parsed.chunks[1:]
        assert later and all(c.symbol == "huge" and c.kind == "function" for c in later)
        cut_inside_a_line = [
            (before, after) for before, after in zip(parsed.chunks, later) if after.starts_mid_line
        ]
        assert cut_inside_a_line  # some cuts land after a statement's indentation, mid-line
        for before, after in cut_inside_a_line:
            assert before.end_line == after.start_line  # so the two chunks share a line number

    def test_oversized_leaf_is_split_inside_a_token(self) -> None:
        source = 'x = "' + "a" * 500 + '"\ny = 2\n'

        parsed = parse_source(source, "python", chunk_max_bytes=100)

        _assert_chunks_tile_source(source, parsed, 100)
        assert sum(1 for c in parsed.chunks if c.start_line == 1) >= 5  # one string literal, many chunks


# --- normalized chunks -------------------------------------------------------------------------


def _expected_lines(data: bytes, start: int, end: int) -> tuple[int, int]:
    """Independent line numbers for a byte range: count newlines instead of using the adapter's index."""
    return data[:start].count(b"\n") + 1, data[: end - 1].count(b"\n") + 1


def _assert_exact(source: str, chunked: ChunkedSource, max_bytes: int) -> None:
    """The chunks tile the source exactly, within the size bound, with accurate positions."""
    data = source.encode("utf-8")
    chunks = chunked.chunks
    if not data:
        assert chunks == ()
        return
    assert chunks[0].start_byte == 0 and chunks[-1].end_byte == len(data)
    for before, after in zip(chunks, chunks[1:]):
        assert before.end_byte == after.start_byte
        assert before.ends_mid_line == after.starts_mid_line  # both describe the same cut
    for chunk in chunks:
        assert 0 < chunk.end_byte - chunk.start_byte <= max_bytes
        assert data[chunk.start_byte : chunk.end_byte].decode("utf-8") == chunk.content
        assert (chunk.start_line, chunk.end_line) == _expected_lines(data, chunk.start_byte, chunk.end_byte)
        assert chunk.parser == chunked.parser and chunk.language == chunked.language
    assert "".join(c.content for c in chunks) == source


def test_normalized_ast_chunks() -> None:
    source = "import os\nimport sys\n"
    imports_only = chunk_source(source, "python", chunk_max_bytes=100)

    assert (imports_only.parser, imports_only.fallback_reason, imports_only.syntax_errors) == ("ast", None, 0)
    (only,) = imports_only.chunks
    assert (only.language, only.kind, only.symbol, only.parser) == ("python", "imports", None, "ast")
    assert (only.start_line, only.end_line, only.start_byte, only.end_byte) == (1, 2, 0, len(source))
    assert only.content == source and not only.starts_mid_line and not only.ends_mid_line

    mixed = (
        "import os\n\n"
        "class A:\n    def m(self):\n        return '" + "x" * 60 + "'\n\n"
        "def f():\n    return '" + "y" * 60 + "'\n\n"
        "VALUE = 1\n"
    )
    chunked = chunk_source(mixed, "python", chunk_max_bytes=110)
    assert [(c.kind, c.symbol) for c in chunked.chunks] == [("imports", None), ("class", "A"), ("function", "f")]
    assert [(c.start_line, c.end_line) for c in chunked.chunks] == [(1, 2), (3, 6), (7, 10)]
    _assert_exact(mixed, chunked, 110)

    (statements,) = chunk_source("A = 1\nB = 2\n", "python").chunks
    assert (statements.kind, statements.symbol) == ("code", None)

    # a class split by the size limit: every piece still belongs to it (or to the method it is inside)
    split = chunk_source(PYTHON, "python", chunk_max_bytes=120)
    assert len(split.chunks) > 1 and split.chunks[0].symbol == "Greeter"
    assert {c.symbol for c in split.chunks[:-1]} <= {"Greeter", "__init__", "hello"}
    _assert_exact(PYTHON, split, 120)


@pytest.mark.parametrize(("language", "source"), [("python", PYTHON), ("javascript", JAVASCRIPT), ("typescript", TYPESCRIPT), ("tsx", TSX)])
def test_every_language_yields_exact_normalized_chunks(language: str, source: str) -> None:
    for size in (60, 150, 1500):
        chunked = chunk_source(source, language, chunk_max_bytes=size)

        assert chunked.parser == "ast" and chunked.language == language
        assert all(c.kind and c.language == language for c in chunked.chunks)
        _assert_exact(source, chunked, size)


def test_oversized_definition_keeps_exact_offsets_and_marks_partial_lines() -> None:
    source = "def huge():\n" + "".join(f"    v{i} = {i} * 2\n" for i in range(40))

    chunked = chunk_source(source, "python", chunk_max_bytes=300)

    _assert_exact(source, chunked, 300)
    pieces = [c for c in chunked.chunks if c.symbol == "huge"]
    assert len(pieces) == len(chunked.chunks) >= 3
    partial = [c for c in pieces if c.starts_mid_line]
    assert partial  # the library cut inside a line
    data = source.encode()
    for chunk in partial:
        # the line range alone is ambiguous (the previous chunk shares the line); the byte range is not
        previous = next(c for c in pieces if c.end_byte == chunk.start_byte)
        assert previous.end_line == chunk.start_line and previous.content != chunk.content
        assert data[chunk.start_byte : chunk.end_byte].decode() == chunk.content


def test_unicode_and_crlf_positions_are_byte_exact_in_both_modes() -> None:
    source = 'naïve = "héllo wörld"\r\ndef größe():\r\n    return "日本語"\r\n\r\ndef b():\r\n    return 2\r\n'

    for language in ("python", "cobol"):  # AST path and fallback path
        chunked = chunk_source(source, language, chunk_max_bytes=40)

        assert chunked.parser == ("ast" if language == "python" else "fallback")
        _assert_exact(source, chunked, 40)
        assert len(source.encode()) > len(source)


def test_content_is_preserved_for_every_kind_of_input() -> None:
    sources = [
        PYTHON, JAVASCRIPT, TYPESCRIPT, TSX, THREE_CLASSES,
        "def f(:\n    return 1\nclass A:\n  def ok(self): pass\nx = (1,\n",  # syntax errors
        "x = 1",  # no trailing newline
        "\n\n  \n",  # whitespace only
        "# comment only\n",
        "\ufeffx = 1\n",  # BOM
        "a\rb\r\nc\n",  # lone and CRLF carriage returns
        "",
    ]
    for source in sources:
        for language in ("python", "typescript", "unknown-lang"):
            for size in (16, 64, 1500):
                _assert_exact(source, chunk_source(source, language, chunk_max_bytes=size), size)


def test_syntax_errors_keep_ast_mode_with_complete_chunks() -> None:
    source = "def f(:\n    return 1\nclass A:\n  def ok(self): pass\nx = (1,\n"

    chunked = chunk_source(source, "python", chunk_max_bytes=60)

    assert chunked.parser == "ast" and chunked.syntax_errors == 2
    _assert_exact(source, chunked, 60)


# --- fallback ----------------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["cobol", "json", "Python", "", None])
def test_unsupported_language_uses_line_fallback_and_never_calls_the_library(
    language: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tslp, "process", lambda *_a, **_k: pytest.fail("process() must not be reached"))
    source = "line one\nline two\nline three\n"

    chunked = chunk_source(source, language, chunk_max_bytes=20)

    assert (chunked.parser, chunked.fallback_reason) == ("fallback", "unsupported_language")
    assert chunked.language == ((language or "").strip() or "text")
    assert all(c.kind == "text" and c.symbol is None and c.parser == "fallback" for c in chunked.chunks)
    assert [c.content for c in chunked.chunks] == ["line one\nline two\n", "line three\n"]  # whole lines grouped
    assert [(c.start_line, c.end_line) for c in chunked.chunks] == [(1, 2), (3, 3)]
    _assert_exact(source, chunked, 20)


def test_missing_grammar_falls_back_without_downloading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tslp, "downloaded_languages", lambda: [])
    monkeypatch.setattr(tslp, "process", lambda *_a, **_k: pytest.fail("process() must not be reached"))
    monkeypatch.setattr(tslp, "download", lambda *_a, **_k: pytest.fail("a grammar must never be downloaded here"))

    chunked = chunk_source(PYTHON, "python", chunk_max_bytes=100)

    assert (chunked.parser, chunked.fallback_reason) == ("fallback", "grammar_missing")
    _assert_exact(PYTHON, chunked, 100)


def test_parser_failures_fall_back_and_preserve_content(monkeypatch: pytest.MonkeyPatch) -> None:
    too_big = chunk_source(PYTHON, "python", chunk_max_bytes=100, max_source_bytes=20)
    assert (too_big.parser, too_big.fallback_reason) == ("fallback", "parse_failed")
    _assert_exact(PYTHON, too_big, 100)

    def library_error(*_args: object, **_kwargs: object) -> None:
        raise tslp.ParseFailedError("could not parse")

    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("native crash")

    class PanicException(BaseException):  # how pyo3 surfaces a Rust panic
        pass

    def panic(*_args: object, **_kwargs: object) -> None:
        raise PanicException("end byte index out of bounds")

    for failing in (library_error, unexpected, panic):
        monkeypatch.setattr(tslp, "process", failing)
        failed = chunk_source(PYTHON, "python", chunk_max_bytes=100)
        assert (failed.parser, failed.fallback_reason) == ("fallback", "parse_failed")
        _assert_exact(PYTHON, failed, 100)


def test_other_base_exceptions_are_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupt(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(tslp, "process", interrupt)

    with pytest.raises(KeyboardInterrupt):
        chunk_source(PYTHON, "python", chunk_max_bytes=100)


def test_inconsistent_parser_chunks_are_rejected_and_replaced_by_the_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(start: int, end: int) -> SimpleNamespace:
        return SimpleNamespace(start_byte=start, end_byte=end, metadata=None)

    data = b"abcdefghi"
    with pytest.raises(InvalidChunksError):
        _ast_chunks([fake(0, 3), fake(5, 9)], data, "python", ())  # gap
    with pytest.raises(InvalidChunksError):
        _ast_chunks([fake(0, 5), fake(4, 9)], data, "python", ())  # overlap
    with pytest.raises(InvalidChunksError):
        _ast_chunks([fake(0, 3)], data, "python", ())  # does not cover the source
    with pytest.raises(InvalidChunksError):
        _ast_chunks([], data, "python", ())  # no chunks for non-empty source
    with pytest.raises(InvalidChunksError):
        _ast_chunks([fake(0, 2), fake(2, 3)], "日本".encode(), "python", ())  # cut inside a character

    def always_invalid(*_args: object, **_kwargs: object) -> None:
        raise InvalidChunksError("bad")

    monkeypatch.setattr("apps.api.services.code_parser._ast_chunks", always_invalid)
    chunked = chunk_source(PYTHON, "python", chunk_max_bytes=100)
    assert (chunked.parser, chunked.fallback_reason) == ("fallback", "invalid_chunks")
    _assert_exact(PYTHON, chunked, 100)


def test_fallback_splits_long_lines_only_at_safe_boundaries() -> None:
    emoji_line = "日" * 40 + "\n"  # 3 bytes per character
    crlf_line = "a" * 21 + "\r\n" + "b" * 5
    source = emoji_line + crlf_line

    chunked = chunk_source(source, "unknown-lang", chunk_max_bytes=16)

    _assert_exact(source, chunked, 16)  # every chunk decodes as UTF-8 on its own
    contents = [c.content for c in chunked.chunks]
    assert all(len(c.encode()) <= 16 for c in contents)
    assert not any(c.startswith("\n") for c in contents)  # never separated a \r from its \n
    assert chunked.chunks[0].starts_mid_line is False and chunked.chunks[1].starts_mid_line is True


def test_fallback_on_empty_and_single_line_sources() -> None:
    assert chunk_source("", "cobol").chunks == ()
    (only,) = chunk_source("no newline", "cobol").chunks
    assert (only.start_line, only.end_line, only.start_byte, only.end_byte) == (1, 1, 0, 10)


# --- final verification (Phase 4.3) ------------------------------------------------------------

TS_MANY = '''import type { Foo } from "./foo";
export interface User { id: number; name: string }
export type Id = string | number;
enum Color { Red, Green }
export const LIMIT = 10;
export class Repo<T> implements Store {
  async get(id: number): Promise<T | undefined> { return this.items[id]; }
}
export function helper<T>(x: T): T { return x; }
'''


def _all_names(symbols: tuple[Symbol, ...]) -> list[str]:
    return [n for s in symbols for n in ([s.name] if s.name else []) + _all_names(s.children)]


def test_a_chunk_with_several_symbols_exposes_all_of_them() -> None:
    (chunk,) = chunk_source(TS_MANY, "typescript", chunk_max_bytes=2000).chunks

    assert chunk.symbol == "User"  # the primary (first) symbol
    # every symbol, in source order: interface, type alias, enum, const, class, its method, function
    assert chunk.symbols == ("User", "Id", "Color", "LIMIT", "Repo", "get", "helper")

    (python,) = chunk_source(PYTHON, "python", chunk_max_bytes=2000).chunks
    assert python.symbols == ("Greeter", "__init__", "hello", "main")  # nested methods included


@pytest.mark.parametrize(
    ("language", "source"),
    [("python", THREE_CLASSES), ("python", PYTHON), ("javascript", JAVASCRIPT), ("typescript", TS_MANY), ("tsx", TSX)],
)
def test_no_symbol_is_lost_whatever_the_chunk_size(language: str, source: str) -> None:
    expected = set(_all_names(parse_source(source, language).symbols))

    for size in (16, 64, 200, 1500):
        chunked = chunk_source(source, language, chunk_max_bytes=size)

        seen = {name for chunk in chunked.chunks for name in chunk.symbols}
        assert expected <= seen, (language, size, expected - seen)


def test_split_pieces_of_a_definition_carry_its_name() -> None:
    source = "def huge():\n" + "".join(f"    v{i} = {i} * 2\n" for i in range(40))

    chunked = chunk_source(source, "python", chunk_max_bytes=100)

    assert len(chunked.chunks) > 3
    assert all(c.symbol == "huge" and "huge" in c.symbols and c.kind == "function" for c in chunked.chunks)


def test_fallback_chunks_have_no_symbols() -> None:
    assert all(c.symbols == () for c in chunk_source("a\nb\n", "cobol").chunks)


def test_symbol_order_follows_the_source_and_names_are_not_repeated() -> None:
    source = "class A:\n    def m(self): pass\n    def n(self): pass\n\nclass B:\n    def m(self): pass\n"

    (chunk,) = chunk_source(source, "python", chunk_max_bytes=2000).chunks

    assert chunk.symbols == ("A", "m", "n", "B")  # `m` appears in both classes but is listed once


@pytest.mark.parametrize("language", ["cobol", "python"])
def test_no_download_entry_point_is_ever_touched(language: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unsupported languages and missing grammars must never reach anything that can use the network."""

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("the parser tried to download or load a grammar")

    for name in ("process", "download", "download_all", "download_group", "init", "prefetch", "get_parser", "get_language"):
        monkeypatch.setattr(tslp, name, forbidden)
    monkeypatch.setattr(tslp, "downloaded_languages", lambda: [])  # nothing cached: `python` is "missing"

    chunked = chunk_source("def f():\n    return 1\n", language, chunk_max_bytes=32)

    assert chunked.parser == "fallback"
    assert chunked.fallback_reason == ("unsupported_language" if language == "cobol" else "grammar_missing")


def test_chunk_size_is_configurable_with_a_1500_byte_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.api.config import Settings
    from apps.api.services import code_parser as module

    assert Settings().code_chunk_max_bytes == module.DEFAULT_CHUNK_MAX_BYTES == 1_500
    source = "".join(f"value_{i} = {i}\n" for i in range(500))

    default = chunk_source(source, "python")
    explicit = chunk_source(source, "python", chunk_max_bytes=300)
    assert 1_000 < max(c.end_byte - c.start_byte for c in default.chunks) <= 1_500
    assert max(c.end_byte - c.start_byte for c in explicit.chunks) <= 300

    monkeypatch.setattr(module, "get_settings", lambda: Settings(code_chunk_max_bytes=200))
    from_settings = chunk_source(source, "python")
    assert max(c.end_byte - c.start_byte for c in from_settings.chunks) <= 200
    monkeypatch.setattr(module, "get_settings", lambda: Settings(code_chunk_max_bytes=15))
    with pytest.raises(ValueError):
        chunk_source(source, "python")


def test_text_that_is_not_valid_unicode_is_rejected_clearly() -> None:
    for call in (chunk_source, parse_source):
        with pytest.raises(ValueError, match="lone surrogate"):
            call("a = 1\n\ud800\n", "python")  # type: ignore[operator]
    # NUL bytes, by contrast, are ordinary text for this module
    nul = "x = 1\0\ny = 2\n"
    assert "".join(c.content for c in chunk_source(nul, "python", chunk_max_bytes=32).chunks) == nul


def test_real_repository_files_chunk_exactly_in_both_modes() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    paths = [
        "apps/api/services/ingestion.py",
        "apps/api/services/code_parser.py",
        "tests/test_ingestion_reliability.py",
        "apps/web/lib/highlight.ts",
        "apps/web/components/workspace/repository-gate.tsx",
        *(str(p.relative_to(root)) for p in (root / "apps/web").glob("*.mjs")),
    ]
    for path in paths:
        language = language_for_path(path)
        raw = (root / path).read_text(encoding="utf-8")
        for source in (raw, raw.replace("\n", "\r\n")):
            for size in (16, 200, 1_500):
                for mode in (language, "cobol"):
                    chunked = chunk_source(source, mode, chunk_max_bytes=size)

                    assert chunked.parser == ("ast" if mode == language else "fallback"), path
                    _assert_exact(source, chunked, size)


def test_chunking_from_worker_threads_gives_identical_results() -> None:
    import asyncio

    samples = {"python": PYTHON, "javascript": JAVASCRIPT, "typescript": TS_MANY, "tsx": TSX}
    baseline = {lang: chunk_source(src, lang, chunk_max_bytes=120) for lang, src in samples.items()}

    async def run() -> list[ChunkedSource]:
        jobs = [
            asyncio.to_thread(chunk_source, samples[lang], lang, chunk_max_bytes=120)
            for _ in range(10)
            for lang in samples
        ]
        return list(await asyncio.gather(*jobs))

    results = asyncio.run(run())

    languages = list(samples) * 10
    assert all(result == baseline[lang] for result, lang in zip(results, languages))
