"""Tests for turning a snapshot's files into chunks (no database)."""

import hashlib
import logging
from pathlib import Path

import pytest
import tree_sitter_language_pack as tslp

from apps.api.services import code_indexing
from apps.api.services.archive_reader import Category
from apps.api.services.archive_reader import SourceFile as ArchiveFile
from apps.api.services.code_indexing import index_entries
from apps.api.services.code_parser import ensure_grammars, missing_grammars


@pytest.fixture(scope="module", autouse=True)
def _grammars_available() -> None:
    if missing_grammars():
        try:
            ensure_grammars()
        except Exception as exc:  # offline: skip rather than fail
            pytest.skip(f"tree-sitter grammars unavailable: {exc}")


def _file(path: str, content: str, language: str = "unknown") -> ArchiveFile:
    return ArchiveFile(
        path=path, language=language, category=Category.SOURCE, content=content,
        sha256=hashlib.sha256(content.encode()).hexdigest(), size_bytes=len(content.encode()),
        line_count=content.count("\n"),
    )


PY = "import os\n\nclass A:\n    def m(self):\n        return 1\n\ndef f():\n    return 2\n"
TS = "export interface User { id: number }\nexport function helper(x: number): number { return x; }\n"
TSX = "export const Card = ({ t }: { t: string }) => <div>{t}</div>;\n"
JS = "export function add(a, b) { return a + b; }\n"
GO = 'package main\n\nfunc main() {\n    println("hi")\n}\n'


def _assert_exact(entry: ArchiveFile, chunks: tuple, max_bytes: int) -> None:
    data = entry.content.encode()
    assert "".join(c.content for c in chunks) == entry.content
    assert chunks[0].start_byte == 0 and chunks[-1].end_byte == len(data)
    assert all(0 < c.end_byte - c.start_byte <= max_bytes for c in chunks)
    assert all(data[c.start_byte : c.end_byte].decode() == c.content for c in chunks)


def test_supported_languages_get_ast_chunks_with_the_parser_language() -> None:
    entries = [
        _file("app.py", PY, "python"), _file("lib.ts", TS, "typescript"),
        _file("Card.tsx", TSX, "typescript"),  # the archive reader calls .tsx "typescript"; the grammar is tsx
        _file("index.js", JS, "javascript"),
    ]

    index = index_entries(entries, chunk_max_bytes=1_500)

    assert {p: {c.language for c in chunks} for p, chunks in index.chunks_by_path.items()} == {
        "app.py": {"python"}, "lib.ts": {"typescript"}, "Card.tsx": {"tsx"}, "index.js": {"javascript"},
    }
    assert all(c.parser == "ast" for chunks in index.chunks_by_path.values() for c in chunks)
    assert (index.stats.files, index.stats.ast_files, index.stats.fallback_files, index.stats.failed_files) == (4, 4, 0, 0)
    assert index.chunks_by_path["app.py"][0].symbols == ("A", "m", "f")


def test_unsupported_languages_use_the_text_fallback_labelled_with_their_language() -> None:
    entries = [
        _file("main.go", GO, "go"), _file("data.json", '{"a": 1}\n', "json"), _file("README.md", "# hi\n", "markdown"),
        _file("Dockerfile", "FROM python\n", "dockerfile"), _file("notes.xyz", "plain\n", "unknown"),
    ]

    index = index_entries(entries)

    assert {p: ({c.language for c in ch}, {c.parser for c in ch}, {c.kind for c in ch})
            for p, ch in index.chunks_by_path.items()} == {
        "main.go": ({"go"}, {"fallback"}, {"text"}), "data.json": ({"json"}, {"fallback"}, {"text"}),
        "README.md": ({"markdown"}, {"fallback"}, {"text"}), "Dockerfile": ({"dockerfile"}, {"fallback"}, {"text"}),
        "notes.xyz": ({"unknown"}, {"fallback"}, {"text"}),
    }
    assert index.stats.fallback_files == 5 and index.stats.fallback_reasons == {"unsupported_language": 5}


def test_every_file_is_chunked_exactly_within_the_size_bound() -> None:
    big = "".join(f"def f{i}(x):\n    return x + {i}\n\n" for i in range(200))
    entries = [_file("big.py", big, "python"), _file("main.go", GO * 20, "go"), _file("Card.tsx", TSX * 30, "typescript")]

    for size in (40, 300, 1_500):
        index = index_entries(entries, chunk_max_bytes=size)
        for entry in entries:
            _assert_exact(entry, index.chunks_by_path[entry.path], size)
        assert index.stats.chunks == sum(len(c) for c in index.chunks_by_path.values())


def test_empty_files_have_no_chunks_but_are_counted() -> None:
    index = index_entries([_file("empty.py", "", "python"), _file("a.py", "x = 1\n", "python")])

    assert set(index.chunks_by_path) == {"a.py"} and index.stats.files == 2 and index.stats.chunks == 1


def test_chunk_size_comes_from_settings_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.api.config import Settings
    from apps.api.services import code_parser

    monkeypatch.setattr(code_parser, "get_settings", lambda: Settings(code_chunk_max_bytes=100))
    entry = _file("big.py", "".join(f"v{i} = {i}\n" for i in range(300)), "python")

    chunks = index_entries([entry]).chunks_by_path["big.py"]

    assert max(c.end_byte - c.start_byte for c in chunks) <= 100


def test_a_parser_failure_on_one_file_falls_back_and_never_aborts_the_run(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    real = code_indexing.chunk_source

    def flaky(source: str, language: str | None, **kwargs: object):  # type: ignore[no-untyped-def]
        if "BOOM" in source and language is not None:
            raise RuntimeError("parser exploded")
        return real(source, language, **kwargs)

    monkeypatch.setattr(code_indexing, "chunk_source", flaky)
    entries = [_file("ok.py", PY, "python"), _file("bad.py", "BOOM = 1\n", "python"), _file("ok2.ts", TS, "typescript")]

    with caplog.at_level(logging.WARNING):
        index = index_entries(entries)

    assert [c.parser for c in index.chunks_by_path["bad.py"]] == ["fallback"]  # retried as plain text
    assert index.chunks_by_path["ok.py"][0].parser == "ast" and index.chunks_by_path["ok2.ts"][0].parser == "ast"
    assert index.stats.failed_files == 0 and "bad.py failed" in caplog.text


def test_a_file_that_cannot_be_chunked_at_all_is_skipped_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    real = code_indexing.chunk_source

    def broken(source: str, language: str | None, **kwargs: object):  # type: ignore[no-untyped-def]
        if "BOOM" in source:
            raise RuntimeError("still broken")
        return real(source, language, **kwargs)

    monkeypatch.setattr(code_indexing, "chunk_source", broken)

    index = index_entries([_file("bad.py", "BOOM = 1\n", "python"), _file("ok.py", PY, "python")])

    assert set(index.chunks_by_path) == {"ok.py"} and index.stats.failed_files == 1 and index.stats.files == 2


def test_missing_grammars_fall_back_without_downloading(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def forbidden(*_a: object, **_k: object) -> None:
        pytest.fail("a grammar must never be downloaded or loaded while indexing")

    for name in ("process", "download", "download_all", "download_group", "init", "prefetch", "get_parser"):
        monkeypatch.setattr(tslp, name, forbidden)
    monkeypatch.setattr(tslp, "downloaded_languages", lambda: [])

    with caplog.at_level(logging.WARNING):
        index = index_entries([_file("a.py", PY, "python"), _file("b.ts", TS, "typescript")])

    assert index.stats.fallback_reasons == {"grammar_missing": 2} and index.stats.ast_files == 0
    assert all(c.parser == "fallback" for chunks in index.chunks_by_path.values() for c in chunks)
    assert "grammar is not installed" in caplog.text


def test_stats_report_counts_and_duration() -> None:
    index = index_entries([_file("a.py", PY, "python"), _file("m.go", GO, "go")])

    assert (index.stats.files, index.stats.ast_files, index.stats.fallback_files) == (2, 1, 1)
    assert index.stats.chunks == len(index.chunks_by_path["a.py"]) + len(index.chunks_by_path["m.go"])
    assert index.stats.duration_ms > 0


def test_a_real_multi_language_project_is_indexed_exactly() -> None:
    """This repository's own backend (Python) and frontend (TypeScript/TSX) as the sample project."""
    root = Path(__file__).resolve().parents[1]
    suffix_language = {".py": "python", ".ts": "typescript", ".tsx": "typescript", ".mjs": "javascript", ".md": "markdown"}
    entries = [
        _file(str(p.relative_to(root)), p.read_text(encoding="utf-8"), suffix_language[p.suffix])
        for base in ("apps/api/services", "apps/api/models", "apps/web/lib", "apps/web/components/workspace", "docs")
        for p in sorted((root / base).rglob("*"))
        if p.suffix in suffix_language and p.is_file() and "node_modules" not in p.parts and p.stat().st_size < 200_000
    ]
    assert len(entries) > 30

    index = index_entries(entries, chunk_max_bytes=1_500)

    assert index.stats.failed_files == 0 and index.stats.ast_files > 25
    languages = {c.language for chunks in index.chunks_by_path.values() for c in chunks}
    assert {"python", "typescript", "tsx", "markdown"} <= languages
    for entry in entries:
        if entry.content:
            _assert_exact(entry, index.chunks_by_path[entry.path], 1_500)
