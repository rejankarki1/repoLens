"""Deterministic, language-generic call-site excerpts of one stored source file.

The model needs to see *how* a file uses what it imports (a read, a write, a request, a dispatch), not just
the head of the file. This module picks bounded windows of the file's real lines around uses of its imported
names, plus entry/initialization lines, always together with:

* the enclosing definition line of each window (so a call is shown inside the function that makes it);
* the import statements that bind the names those windows use (their line numbers come from the stored
  Tree-sitter file-edge evidence, never from a regex guess about what is an import);
* a few statements of surrounding context.

Every emitted line is verbatim and carries its 1-based line number; omissions are marked explicitly. Operation
words (`read`, `save`, `fetch`, ...) only influence which windows are *selected* — nothing here labels a line
as a call, request or database operation, and no relationship is inferred. Same input, same output.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_QUOTED = re.compile(r"""("[^"\n]*"|'[^'\n]*'|`[^`\n]*`)""")
_NOT_BINDINGS = frozenset({
    "import", "from", "as", "const", "let", "var", "require", "type", "default", "export", "use", "using",
    "include", "typeof", "static", "pub", "crate", "self", "super", "package", "mod", "in", "of", "async", "await",
})
_DEFINITION = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:def|function\*?|class|fn|func|sub)\b"
    r"|^\s*(?:export\s+)?(?:const|let|var)\s+[\w$]+\s*=\s*(?:async\s*)?(?:\([^)]*\)|[\w$]+)\s*=>"
    r"|^\s*(?:public|private|protected|static)\s+[\w<>\[\], ]+\s+[\w$]+\s*\("
)
_ENTRY = re.compile(
    r"__main__|\bmain\s*\(|\bcreate_app\s*\(|\.listen\s*\(|\bserve\s*\(|\bapp\.(?:use|get|post|put|delete|patch|route|include_router)\s*\("
    r"|\brouter\.\w+\s*\(|@(?:app|router)\.\w+|\bnew\s+(?:Express|FastAPI|Flask)\b|\bDeno\.serve\b"
)
_OPERATION = re.compile(
    r"\b(?:load|save|read|write|open|query|fetch|find|create|insert|update|delete|remove|send|post|put|patch|"
    r"request|dispatch|enqueue|publish|train|fit|predict|infer|simulate|run|execute|render|parse|serialize|"
    r"to_csv|read_csv|to_json|dump|connect|commit|emit|submit)\w*\s*\(",
    re.IGNORECASE,
)
_BEFORE = 1
_AFTER = 5
_FALLBACK_HEAD = 6


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _binding_names(statement_lines: Iterable[str]) -> set[str]:
    names: set[str] = set()
    for line in statement_lines:
        for token in _IDENT.findall(_QUOTED.sub(" ", line)):
            if token not in _NOT_BINDINGS and len(token) > 1:
                names.add(token)
    return names


def _enclosing_definition(lines: list[str], index: int) -> int | None:
    """Nearest preceding definition line that is less indented than `lines[index]`."""
    here = _indent(lines[index])
    if here == 0 and not _DEFINITION.match(lines[index]):
        return None
    for i in range(index - 1, -1, -1):
        line = lines[i]
        if line.strip() and _DEFINITION.match(line) and _indent(line) < here:
            return i
    return None


def call_site_excerpt(
    text: str, budget: int, *, import_line_ranges: Iterable[tuple[int, int]] = ()
) -> str:
    """Excerpt of `text` (<= `budget` characters). `import_line_ranges` are 1-based inclusive line ranges of
    the file's import statements, from the stored file-edge evidence. A file that already fits is returned
    whole (numbered like every other excerpt)."""
    lines = text.split("\n")
    if len(text) <= budget:
        whole = "\n".join(f"{i + 1}| {line}" for i, line in enumerate(lines))
        if len(whole) <= budget:
            return whole
    import_idx: set[int] = set()
    for start, end in import_line_ranges:
        import_idx.update(i for i in range(max(start, 1) - 1, min(end, len(lines))))
    bindings = _binding_names(lines[i] for i in sorted(import_idx))
    call_pattern = (
        re.compile(r"(?<![\w$.])(?:" + "|".join(re.escape(n) for n in sorted(bindings)) + r")\b\s*[(.\[]|<(?:"
                   + "|".join(re.escape(n) for n in sorted(bindings)) + r")[\s/>]")
        if bindings
        else None
    )

    # (score, anchor line index) candidates; deterministic tie-break on position.
    anchors: list[tuple[int, int]] = []
    for i, line in enumerate(lines):
        if i in import_idx or not line.strip():
            continue
        if _ENTRY.search(line):
            anchors.append((90, i))
        elif call_pattern is not None and call_pattern.search(line):
            anchors.append((100 if _OPERATION.search(line) else 60, i))
        elif _OPERATION.search(line):
            anchors.append((40, i))
        elif not bindings and _DEFINITION.match(line) and _indent(line) == 0:
            anchors.append((30, i))
    anchors.sort(key=lambda a: (-a[0], a[1]))

    import_share = int(budget * 0.3) if import_idx else 0
    body_budget = budget - import_share - 40  # headroom for omission markers
    chosen: set[int] = set(range(min(_FALLBACK_HEAD, len(lines)))) if not import_idx and not anchors else set()
    used = sum(len(lines[i]) + 7 for i in chosen)
    for _, anchor in anchors:
        window = set(range(max(0, anchor - _BEFORE), min(len(lines), anchor + _AFTER + 1)))
        enclosing = _enclosing_definition(lines, anchor)
        if enclosing is not None:
            window.add(enclosing)
        add = [i for i in window if i not in chosen and i not in import_idx]
        cost = sum(len(lines[i]) + 7 for i in add)
        if not add or used + cost > body_budget:
            continue
        chosen.update(add)
        used += cost

    # Only the import statements that bind a name the chosen lines actually use.
    body_names = _binding_names(lines[i] for i in chosen)
    imports_used: list[int] = []
    import_used_chars = 0
    for i in sorted(import_idx):
        if not (_binding_names([lines[i]]) & body_names):
            continue
        cost = len(lines[i]) + 7
        if import_used_chars + cost > import_share + (body_budget - used):
            break
        imports_used.append(i)
        import_used_chars += cost

    out: list[str] = []
    previous = -2
    for i in sorted(chosen | set(imports_used)):
        if i != previous + 1:
            out.append(f"⋯ [lines {previous + 2}-{i} omitted]" if previous >= 0 else f"⋯ [lines 1-{i} omitted]" if i > 0 else "")
        out.append(f"{i + 1}| {lines[i]}")
        previous = i
    if previous < len(lines) - 1:
        out.append(f"⋯ [lines {previous + 2}-{len(lines)} omitted]")
    out = [o for o in out if o]
    while out and len("\n".join(out)) > budget:  # drop whole trailing lines; never emit a partial line
        out.pop()
    return "\n".join(out)
