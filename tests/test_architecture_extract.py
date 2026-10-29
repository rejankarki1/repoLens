"""Static import extraction: what is found, with which relationship and lines, and what is deliberately ignored."""

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from apps.api.services.architecture import extract
from apps.api.services.architecture.extract import ExtractionError, ImportExtractor


@pytest.fixture(scope="module")
def extractor() -> ImportExtractor:
    return ImportExtractor()


def found(extraction):
    return [(i.start_line, i.end_line, i.relationship, i.specifier, i.names) for i in extraction.imports]


def test_python_imports_relative_imports_and_lines(extractor) -> None:
    source = textwrap.dedent('''\
        import os, sys as system
        import a.b.c
        from . import sibling
        from .pkg import thing as t, other
        from ..up import (
            x,
            y,
        )
        from m import *
        from __future__ import annotations
        ''')
    assert found(extractor.extract(source, "python")) == [
        (1, 1, "imports", "os", ()), (1, 1, "imports", "sys", ()), (2, 2, "imports", "a.b.c", ()),
        (3, 3, "imports", ".", ("sibling",)), (4, 4, "imports", ".pkg", ("thing", "other")),
        (5, 8, "imports", "..up", ("x", "y")), (9, 9, "imports", "m", ("*",)),
    ]


def test_python_lazy_and_type_checking_imports_are_not_eager(extractor) -> None:
    source = textwrap.dedent('''\
        from typing import TYPE_CHECKING
        if TYPE_CHECKING:
            from .types import T
        else:
            import runtime_only
        def f():
            import inner
        class C:
            import at_class_level
        ''')
    kinds = {i.specifier: i.relationship for i in extractor.extract(source, "python").imports}
    assert kinds == {"typing": "imports", ".types": "type_only", "runtime_only": "imports", "inner": "lazy_import", "at_class_level": "imports"}


def test_typescript_import_forms(extractor) -> None:
    source = textwrap.dedent('''\
        import React, { useState } from "react";
        import type { Foo } from './types';
        import "./side-effect.css";
        import {
          a,
          b,
        } from '../multi/line';
        export * from './reexp';
        export { z } from './z';
        export type { Q } from './q';
        export const k = 1;
        const m = require('./cjs');
        const lazy = () => import('./lazy');
        import fs = require('fs');
        ''')
    assert [(i.start_line, i.end_line, i.relationship, i.specifier) for i in extractor.extract(source, "typescript").imports] == [
        (1, 1, "imports", "react"), (2, 2, "type_only", "./types"), (3, 3, "imports", "./side-effect.css"),
        (4, 7, "imports", "../multi/line"), (8, 8, "reexports", "./reexp"), (9, 9, "reexports", "./z"),
        (10, 10, "type_only", "./q"), (12, 12, "requires", "./cjs"), (13, 13, "lazy_import", "./lazy"), (14, 14, "requires", "fs"),
    ]


def test_computed_require_and_import_are_counted_not_guessed(extractor) -> None:
    result = extractor.extract("const a = require(name);\nawait import(`./${dir}/x`);\nimport(other);\nrequire('./ok');\n", "javascript")
    assert [i.specifier for i in result.imports] == ["./ok"]
    assert result.dynamic_skipped == 3


def test_template_literal_without_substitution_is_a_literal(extractor) -> None:
    assert [i.specifier for i in extractor.extract("import(`./plain`);\n", "typescript").imports] == ["./plain"]


def test_nested_call_arguments_do_not_confuse_the_outer_call(extractor) -> None:
    result = extractor.extract("const x = require(pick('./a'));\nconst y = require('./b', 'ignored');\n", "javascript")
    assert [i.specifier for i in result.imports] == ["./b"]
    assert result.dynamic_skipped == 1


def test_tsx_and_jsx_syntax(extractor) -> None:
    source = 'import { Btn } from "@/components/ui/button"\nexport function App() { return <Btn label={"x"} /> }\n'
    assert [i.specifier for i in extractor.extract(source, "tsx").imports] == ["@/components/ui/button"]
    assert [i.specifier for i in extractor.extract("import a from './a.js'\nconst el = <div/>\n", "javascript").imports] == ["./a.js"]


def test_syntax_errors_are_flagged_but_imports_still_found(extractor) -> None:
    result = extractor.extract("import a from './a'\nfunction (((\n", "typescript")
    assert result.syntax_errors and [i.specifier for i in result.imports] == ["./a"]


def test_unsupported_language_and_oversized_files_raise(extractor) -> None:
    with pytest.raises(ExtractionError):
        extractor.extract("x", "ruby")
    with pytest.raises(ExtractionError):
        ImportExtractor(max_source_bytes=10).extract("import a from './a'\n" * 5, "typescript")


def test_non_ascii_source_keeps_line_numbers(extractor) -> None:
    source = "# comentário ✓ 日本語\nimport ünï\nfrom pkg import ñame\n"
    assert [(i.start_line, i.specifier) for i in extractor.extract(source, "python").imports] == [(2, "ünï"), (3, "pkg")]


def test_extraction_survives_a_realistic_file_in_a_subprocess() -> None:
    """The installed py-tree-sitter segfaulted (sometimes only at interpreter exit) on a real file full of
    `await import(...)` when node.start_point/end_point were read. A native crash would kill pytest itself, so the
    check runs in a child process and requires a clean exit."""
    script = textwrap.dedent('''\
        from apps.api.services.architecture.extract import ImportExtractor
        lines = ["import type { A } from '@/types';", "import { b } from './b';", "export async function run() {"]
        lines += [f"  const {{ m{i} }} = await import('pkg-{i % 7}');" for i in range(60)]
        lines += ["  const c = require('./cjs');", "  return a;", "}"]
        extractor = ImportExtractor()
        for _ in range(20):
            result = extractor.extract("\\n".join(lines) * 3, "typescript")
        assert len(result.imports) > 100, len(result.imports)
        print("clean")
        ''')
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120, cwd=Path(__file__).resolve().parent.parent)
    assert done.returncode == 0 and done.stdout.strip() == "clean", (done.returncode, done.stderr[-500:])


def test_extractor_never_reads_the_unsafe_tree_sitter_getters() -> None:
    """`node.start_point`, `node.end_point` and `node.text` are avoided (see _LineIndex); keep it that way."""
    tree = ast.parse(Path(extract.__file__).read_text())
    used = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not used & {"start_point", "end_point", "text"}
