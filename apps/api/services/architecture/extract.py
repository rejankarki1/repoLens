"""Static import extraction from source text with tree-sitter queries.

Only imports whose target is written literally in the source are returned: `import x from "./a"`,
`export * from "./b"`, `require("./c")`, `import("./d")` (string or plain template literal), and Python
`import` / `from ... import`. A `require(name)` or `import(name)` with a computed argument is counted, never
guessed. Each import carries its exact 1-based inclusive line range.

Relationships: `imports`, `reexports` (`export ... from`), `requires` (CommonJS), `lazy_import` (JS `import()`,
or a Python import inside a function body) and `type_only` (`import type`, Python `if TYPE_CHECKING:`). The
lazy and type-only kinds are not runtime-eager edges, which matters for cycle detection.

Only allowlisted, already-downloaded grammars are used (see `code_parser`): nothing is downloaded here.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from dataclasses import dataclass, field

import tree_sitter_language_pack as tslp
from tree_sitter import Node, Query, QueryCursor

from apps.api.services.code_parser import DEFAULT_MAX_SOURCE_BYTES, SUPPORTED_LANGUAGES, missing_grammars

IMPORTS = "imports"
REEXPORTS = "reexports"
REQUIRES = "requires"
LAZY = "lazy_import"
TYPE_ONLY = "type_only"

_SNIPPET_MAX = 160
_TYPE_PREFIX = re.compile(rb"^(?:import|export)\s+type[\s{*]")
_WS = re.compile(r"\s+")

_PY_QUERY = "(import_statement) @import (import_from_statement) @from"
_JS_QUERY = """
(import_statement) @import
(export_statement) @export
(call_expression function: (identifier) @fn (#eq? @fn "require") arguments: (arguments . (_) @arg)) @require
(call_expression function: (import) arguments: (arguments . (_) @arg)) @dynamic
(call_expression function: (identifier) @fn (#eq? @fn "require") arguments: (arguments)) @require_any
(call_expression function: (import) arguments: (arguments)) @dynamic_any
"""
_TS_QUERY = _JS_QUERY + "(import_require_clause) @importeq\n"


class ExtractionError(Exception):
    """The file could not be analyzed (unsupported language, missing grammar, too large, parser failure)."""


@dataclass(frozen=True, slots=True)
class RawImport:
    language: str
    relationship: str
    specifier: str  # JS: the module string. Python: dotted module name, with leading dots when relative
    names: tuple[str, ...]  # Python `from x import a, b` -> ("a", "b"), "*" for a wildcard; () for `import x` and JS
    start_line: int
    end_line: int
    text: str


@dataclass(slots=True)
class Extraction:
    imports: list[RawImport] = field(default_factory=list)
    syntax_errors: bool = False
    dynamic_skipped: int = 0  # require()/import() with a non-literal argument


def _snippet(text: str) -> str:
    return _WS.sub(" ", text).strip()[:_SNIPPET_MAX]


def _text(data: bytes, node: Node) -> bytes:
    """The node's source bytes, sliced from our own copy of the source."""
    return data[node.start_byte: node.end_byte]


class _LineIndex:
    """Line numbers from byte offsets.

    `node.start_point` / `node.end_point` are deliberately never used: in the installed py-tree-sitter (0.26) reading
    them corrupted memory and crashed the interpreter (a segmentation fault, sometimes only at exit) on a real
    TypeScript file with many `await import(...)` calls. Byte offsets and our own copy of the source are reliable.
    """

    def __init__(self, data: bytes) -> None:
        self._newlines = [m.start() for m in re.finditer(b"\n", data)]
        self.lines = data.decode("utf-8", "replace").split("\n")

    def line_at(self, byte: int) -> int:
        return bisect_left(self._newlines, byte) + 1

    def span(self, node: Node) -> tuple[int, int]:
        return self.line_at(node.start_byte), self.line_at(max(node.end_byte - 1, node.start_byte))


def _string_value(data: bytes, node: Node | None) -> str | None:
    if node is None:
        return None
    if node.type == "string" or (node.type == "template_string" and not any(c.type == "template_substitution" for c in node.children)):
        return _text(data, node).decode("utf-8", "replace")[1:-1]
    return None


class ImportExtractor:
    """Holds one parser and query per language for a whole analysis run (parsers are not shared across threads)."""

    def __init__(self, max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES) -> None:
        self._max = max_source_bytes
        self._missing = set(missing_grammars())
        self._parsers: dict[str, object] = {}
        self._queries: dict[str, Query] = {}

    def _prepare(self, language: str):
        if language not in SUPPORTED_LANGUAGES:
            raise ExtractionError(f"unsupported language {language!r}")
        if language in self._missing:
            raise ExtractionError(f"the {language} grammar is not installed")
        if language not in self._parsers:
            self._parsers[language] = tslp.get_parser(language)
            source = _PY_QUERY if language == "python" else (_JS_QUERY if language == "javascript" else _TS_QUERY)
            self._queries[language] = Query(tslp.get_language(language), source)
        return self._parsers[language], self._queries[language]

    def extract(self, source: str, language: str) -> Extraction:
        parser, query = self._prepare(language)
        data = source.encode("utf-8", "replace")
        if len(data) > self._max:
            raise ExtractionError("file is larger than the analysis limit")
        try:
            tree = parser.parse(data)
        except Exception as exc:  # native parser failure
            raise ExtractionError(f"parser failure ({type(exc).__name__})") from exc
        cursor = QueryCursor(query)  # captured nodes must not outlive their cursor or tree: keep both until we return
        captures = cursor.captures(tree.root_node)
        out = Extraction(syntax_errors=tree.root_node.has_error)
        index = _LineIndex(data)
        if language == "python":
            self._python(captures, data, index, out)
        else:
            self._javascript(captures, language, data, index, out)
        out.imports.sort(key=lambda i: (i.start_line, i.end_line, i.specifier, i.names))
        return out

    # --- Python -----------------------------------------------------------------------------------------

    @staticmethod
    def _py_relationship(data: bytes, node: Node) -> str:
        parent, lazy = node.parent, False
        while parent is not None:
            if parent.type in ("function_definition", "lambda"):
                lazy = True
            elif parent.type == "if_statement":
                condition = parent.child_by_field_name("condition")
                if condition is not None and _text(data, condition).decode().replace(" ", "") in ("TYPE_CHECKING", "typing.TYPE_CHECKING"):
                    consequence = parent.child_by_field_name("consequence")
                    if consequence is not None and consequence.start_byte <= node.start_byte < consequence.end_byte:
                        return TYPE_ONLY
            parent = parent.parent
        return LAZY if lazy else IMPORTS

    def _python(self, captures: dict[str, list[Node]], data: bytes, index: _LineIndex, out: Extraction) -> None:
        for node in captures.get("import", []):
            relationship = self._py_relationship(data, node)
            for name in node.children_by_field_name("name"):
                dotted = name.child_by_field_name("name") if name.type == "aliased_import" else name
                if dotted is None:
                    continue
                first, last = index.span(node)
                out.imports.append(RawImport(
                    "python", relationship, _WS.sub("", _text(data, dotted).decode()), (),
                    first, last, _snippet(_text(data, node).decode("utf-8", "replace")),
                ))
        for node in captures.get("from", []):
            module = node.child_by_field_name("module_name")
            if module is None:
                continue
            specifier = _WS.sub("", _text(data, module).decode())
            names: list[str] = []
            for name in node.children_by_field_name("name"):
                dotted = name.child_by_field_name("name") if name.type == "aliased_import" else name
                if dotted is not None:
                    names.append(_WS.sub("", _text(data, dotted).decode()))
            if any(c.type == "wildcard_import" for c in node.children):
                names.append("*")
            if not names:
                continue
            first, last = index.span(node)
            out.imports.append(RawImport(
                "python", self._py_relationship(data, node), specifier, tuple(names),
                first, last, _snippet(_text(data, node).decode("utf-8", "replace")),
            ))

    # --- JavaScript / TypeScript / TSX ------------------------------------------------------------------

    def _javascript(self, captures: dict[str, list[Node]], language: str, data: bytes, index: _LineIndex, out: Extraction) -> None:
        def add(relationship: str, spec: str, node: Node, text: str) -> None:
            first, last = index.span(node)
            out.imports.append(RawImport(language, relationship, spec, (), first, last, _snippet(text)))

        for node in captures.get("import", []):
            spec = _string_value(data, node.child_by_field_name("source"))
            if spec is not None:  # `import x = require(...)` has no source here; its clause is handled below
                relationship = TYPE_ONLY if _TYPE_PREFIX.match(_text(data, node)) else IMPORTS
                add(relationship, spec, node, _text(data, node).decode("utf-8", "replace"))
        for node in captures.get("export", []):
            spec = _string_value(data, node.child_by_field_name("source"))
            if spec is not None:
                relationship = TYPE_ONLY if _TYPE_PREFIX.match(_text(data, node)) else REEXPORTS
                add(relationship, spec, node, _text(data, node).decode("utf-8", "replace"))
        for node in captures.get("importeq", []):
            spec = _string_value(data, node.child_by_field_name("source"))
            if spec is not None:
                add(REQUIRES, spec, node, index.lines[index.line_at(node.start_byte) - 1])
        # Calls: `@arg` is the first argument, captured by the query itself (node objects from `named_children`
        # proved unreliable in this py-tree-sitter build). A call whose first argument is not a literal is only counted.
        arguments = sorted(captures.get("arg", []), key=lambda a: a.start_byte)
        starts = [a.start_byte for a in arguments]
        literal_calls = 0
        for kind, relationship in (("require", REQUIRES), ("dynamic", LAZY)):
            for node in captures.get(kind, []):
                at = bisect_left(starts, node.start_byte)  # the earliest argument inside the call is its first argument
                first = arguments[at] if at < len(arguments) and arguments[at].end_byte <= node.end_byte else None
                spec = _string_value(data, first)
                if spec is not None:
                    literal_calls += 1
                    add(relationship, spec, node, index.lines[index.line_at(node.start_byte) - 1])
        total_calls = len(captures.get("require_any", [])) + len(captures.get("dynamic_any", []))
        out.dynamic_skipped += max(0, total_calls - literal_calls)
