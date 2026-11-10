"""Repository-structure retrieval for questions that keyword and vector search cannot answer.

"Where is the entry point?", "how does the app start?" or "how are the frontend and backend organised?" name
no symbol, and the answer lives in files whose text shares no words with the question (`server.js` calls
`app.listen(port)`; `main.jsx` calls `createRoot(...).render(...)`). Searching for the question finds nothing,
or finds `app.js`, which defines the app but never starts it. So for these questions, and only these, the
answer's evidence is first *discovered* from the code, then handed to the normal excerpt pipeline:

1. **Discover (deterministic, no model).** Start-up constructs found in the source itself
   (`signals.scan_entrypoints`: a listening server, a UI mount, a script guard, a `main` function) and start-up
   targets declared by manifests (package.json `start`/`main`, pyproject scripts, Procfile, Dockerfile `CMD`,
   `index.html` module scripts), each resolved against the snapshot's real file list. A declared target that
   does not exist is reported as a discrepancy, never used. File *names* alone are never evidence.
2. **Follow one hop.** The files an entry point imports (the existing import extractor and resolver) show what
   it wires together, so "how does the server start" can name the app and database modules it loads.
3. **Cite.** Each piece of evidence is mapped to the stored code chunk that contains it, so it becomes an
   ordinary `[S#]` source with a real path, line range and commit. Nothing here can be cited without being
   shown to the model, and the model still cites only `[S#]` markers.
4. **Say what is uncertain.** A compact, clearly-labelled repository map (layout, manifests, detected stack,
   entry points, and every discrepancy or gap) accompanies the excerpts. It is derived data, never citable.

Everything is bounded: a handful of chunks (each at most `code_chunk_max_bytes`), a map of a few thousand
characters, and a capped content scan. If discovery fails for any reason the question is answered from search
alone and says so.
"""

from __future__ import annotations

import json
import logging
import posixpath
import re
import tomllib
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import Settings, get_settings
from apps.api.models.entities import CodeChunk, SourceFile
from apps.api.services.architecture.extract import ExtractionError, ImportExtractor
from apps.api.services.architecture.resolve import Resolver
from apps.api.services.architecture.signals import (
    detect_technologies, entry_markers, parse_manifests, scan_entrypoints, scannable_for_entrypoints,
)
from apps.api.services.code_parser import language_for_path

logger = logging.getLogger(__name__)

# --- recognising the question ------------------------------------------------------------------------------

_APP = r"(?:app|application|server|backend|back-end|frontend|front-end|client|project|program|service|site|website|api|ui|repo|repository|codebase)"
_STRUCTURAL = re.compile(
    "|".join([
        r"\bentry[\s_-]?points?\b",
        r"\bentrypoints?\b",
        r"\bbootstrap(?:s|ped|ping)?\b",
        r"\bstart[\s-]?up\b",
        r"\bmain\s+(?:file|entry|module|script|function|program)\b",
        rf"\b{_APP}\b[^.?!\n]{{0,40}}\b(?:start|starts|started|starting|begin|begins|launch|launches|boot|boots|initiali[sz]es?)\b",
        rf"\b(?:start|starting|run|running|launch|launching)\s+(?:the\s+|this\s+|my\s+)?{_APP}\b",
        rf"\bwhere\s+(?:does|do|is|are)\b[^.?!\n]{{0,30}}\b(?:start|begin|run|boot|launch)\b",
        rf"\bhow\s+(?:do|can|should)\s+i\s+(?:run|start|launch|boot)\b",
        r"\b(?:project|repo(?:sitory)?|code\s?base|folder|directory|file|source)\s+(?:structure|layout|organi[sz]ation|tree|map)\b",
        r"\b(?:structure|layout|organi[sz]ation)\s+of\s+(?:the\s+|this\s+)?(?:project|repo(?:sitory)?|code\s?base|app|application)\b",
        r"\barchitecture\b",
        r"\bhigh[\s-]level\s+(?:overview|design|structure)\b",
        r"\bhow\s+is\s+(?:the\s+|this\s+)?\w*\s*(?:project|repo(?:sitory)?|code\s?base|app|application)\s+(?:organi[sz]ed|structured|laid\s+out)\b",
        r"\boverview\s+of\s+(?:the\s+|this\s+)?(?:project|repo(?:sitory)?|code\s?base|app|application)\b",
        r"\bwhat\s+does\s+(?:the\s+|this\s+)(?:project|repo(?:sitory)?|code\s?base|app|application)\s+do\b",
        r"\b(?:front[\s-]?end|client|ui)\b[^.?!\n]{0,40}\b(?:back[\s-]?end|server|api)\b",
        r"\b(?:back[\s-]?end|server|api)\b[^.?!\n]{0,40}\b(?:front[\s-]?end|client|ui)\b",
    ]),
    re.IGNORECASE,
)
_FRONTEND = re.compile(r"\b(?:front[\s-]?end|client|ui|react|vue|angular|svelte|browser|web\s?app)\b", re.IGNORECASE)
_BACKEND = re.compile(r"\b(?:back[\s-]?end|server|api|express|fastapi|flask|django|node)\b", re.IGNORECASE)


def is_structural_question(question: str) -> bool:
    """True for questions about where things start or how the repository is organised (conservative on purpose:
    an ordinary question must keep exactly the retrieval it had)."""
    return bool(_STRUCTURAL.search(question))


def area_preference(question: str) -> str | None:
    """`frontend` or `backend` when the question asks about only one of them, else None (show both)."""
    front, back = bool(_FRONTEND.search(question)), bool(_BACKEND.search(question))
    if front and not back:
        return "frontend"
    if back and not front:
        return "backend"
    return None


# --- discovery (pure) --------------------------------------------------------------------------------------


@dataclass(slots=True)
class EntryPoint:
    path: str
    line: int
    kind: str  # server_start | ui_bootstrap | app_object | script_main | declared
    area: str  # backend | frontend | cli | app
    reasons: list[str] = field(default_factory=list)
    declared_by: list[str] = field(default_factory=list)  # e.g. "backend/package.json: scripts.start"
    in_code: bool = False  # a start-up construct was found in the file itself

    @property
    def rank(self) -> tuple[int, int, str]:
        if self.in_code and self.declared_by and self.kind != "script_main":
            tier = 0  # the manifest says it starts here and the code does start something
        elif self.kind in ("server_start", "ui_bootstrap"):
            tier = 1
        elif self.kind in ("declared", "app_object"):
            tier = 2
        else:
            tier = 3
        return tier, self.path.count("/"), self.path


@dataclass(slots=True)
class DeclaredMention:
    """A manifest line that names a start-up target, kept as citable evidence even when it adds no new file."""
    path: str
    line: int
    detail: str


@dataclass(slots=True)
class Discovery:
    entries: list[EntryPoint] = field(default_factory=list)
    declarations: list[DeclaredMention] = field(default_factory=list)
    discrepancies: list[str] = field(default_factory=list)
    technologies: list[str] = field(default_factory=list)


_SCRIPT_NAMES = ("start", "dev", "serve", "develop", "server", "main")
_FILE_TOKEN = re.compile(r"(?<![\w./@-])((?:\.{0,2}/)?[\w@./-]*[\w-]\.(?:[cm]?[jt]sx?|py|go|rs|java|kt|cs|dart))(?![\w-])")
_MODULE_TOKEN = re.compile(r"(?<![\w./@-])([A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)*):[A-Za-z_]\w*")
_DASH_M = re.compile(r"(?<![\w-])-m\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)")  # python -m package.module
_PREFIX = re.compile(r"(?:--prefix|--workspace|-C|--cwd)[= ]+([\w@./-]+)|\bcd\s+([\w@./-]+)\s*&&")
_HTML_SCRIPT = re.compile(r"<script\b[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"'][^>]*>", re.IGNORECASE)


def _line_of(text: str, needle: str) -> int:
    index = text.find(needle)
    return text.count("\n", 0, index) + 1 if index >= 0 else 1


def _join(base: str, token: str) -> str:
    return posixpath.normpath(posixpath.join(base, token)) if base else posixpath.normpath(token)


def _resolve_file(token: str, base: str, paths: set[str]) -> str | None:
    """A file token from a command or HTML tag -> a path that exists in the snapshot (relative to `base`, else the root)."""
    cleaned = token.lstrip("/")
    candidates = [_join(base, cleaned)]
    if token.startswith("/"):
        candidates.append(posixpath.normpath(cleaned))  # a site-root path (`/src/main.jsx`) may be relative to the repository root
    return next((c for c in candidates if c in paths), None)


def _resolve_module(module: str, base: str, paths: set[str]) -> str | None:
    """`pkg.sub.mod` -> `pkg/sub/mod.py` (or its package `__init__`/`__main__`), under `base`, the root, or one unique source root."""
    stem = module.replace(".", "/")
    options = (f"{stem}.py", f"{stem}/__main__.py", f"{stem}/__init__.py")
    for prefix in ((base, "") if base else ("",)):
        for option in options:
            candidate = _join(prefix, option)
            if candidate in paths:
                return candidate
    for option in options:
        matches = sorted(p for p in paths if p.endswith("/" + option))
        if len(matches) == 1:
            return matches[0]
    return None


def _command_targets(command: str, base: str, paths: set[str]) -> list[str]:
    found: list[str] = []
    for token in _FILE_TOKEN.findall(command):
        target = _resolve_file(token, base, paths)
        if target and target not in found:
            found.append(target)
    for module in [*_MODULE_TOKEN.findall(command), *_DASH_M.findall(command)]:
        target = _resolve_module(module, base, paths)
        if target and target not in found:
            found.append(target)
    return found


def _package_json(path: str, text: str, paths: set[str], texts: dict[str, str], out: Discovery, depth: int = 0) -> list[tuple[str, int, str, str]]:
    """(target, line, declared_by, command) for a package.json: its `main`/`bin` and its start-like scripts,
    following `--prefix dir` / `cd dir &&` into the named package. Missing targets become discrepancies."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    base = posixpath.dirname(path)
    found: list[tuple[str, int, str, str]] = []

    main = data.get("main")
    if isinstance(main, str):
        target = _resolve_file(main, base, paths)
        line = _line_of(text, '"main"')
        if target:
            found.append((target, line, f"{path}: main", main))
        else:
            out.discrepancies.append(f'{path} declares "main": "{main[:80]}" but no such file exists in this snapshot')
    bins = data.get("bin")
    for name, value in (({"": bins} if isinstance(bins, str) else bins) if isinstance(bins, (str, dict)) else {}).items():
        target = _resolve_file(value, base, paths) if isinstance(value, str) else None
        if target:
            found.append((target, _line_of(text, '"bin"'), f"{path}: bin", str(value)))

    scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
    for name in sorted(scripts, key=lambda n: (_SCRIPT_NAMES.index(n) if n in _SCRIPT_NAMES else 99, n)):
        command = scripts[name]
        if not isinstance(command, str) or not (name in _SCRIPT_NAMES or name.split(":")[0] in _SCRIPT_NAMES):
            continue
        line = _line_of(text, f'"{name}"')
        prefix = _PREFIX.search(command)
        followed = False
        if prefix and depth < 2:
            directory = _join(base, prefix.group(1) or prefix.group(2))
            inner = f"{directory}/package.json"
            if inner in texts and inner != path:
                inner_found = _package_json(inner, texts[inner], paths, texts, out, depth + 1)
                if inner_found:
                    followed = True
                    found.append(("", line, f"{path}: scripts.{name}", command))  # keeps the root script as evidence
                    found.extend(inner_found)
        for target in _command_targets(command, base, paths):
            found.append((target, line, f"{path}: scripts.{name}", command))
            followed = True
        if not followed:
            found.append(("", line, f"{path}: scripts.{name}", command))
    return found


def _pyproject(path: str, text: str, paths: set[str]) -> list[tuple[str, int, str, str]]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    base = posixpath.dirname(path)
    found: list[tuple[str, int, str, str]] = []
    tables = [(data.get("project") or {}).get("scripts"), ((data.get("tool") or {}).get("poetry") or {}).get("scripts")]
    for table in tables:
        for name, value in (table or {}).items() if isinstance(table, dict) else ():
            if isinstance(value, str):
                target = _resolve_module(value.split(":")[0].strip(), base, paths)
                if target:
                    found.append((target, _line_of(text, name), f"{path}: scripts.{name}", value))
    return found


def _process_file(path: str, text: str, paths: set[str]) -> list[tuple[str, int, str, str]]:
    """Procfile / Dockerfile: the command a platform runs."""
    base, name = posixpath.dirname(path), posixpath.basename(path)
    found: list[tuple[str, int, str, str]] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if name == "Procfile":
            command = line.split(":", 1)[1] if ":" in line and not line.startswith("#") else ""
        else:
            match = re.match(r"(?:CMD|ENTRYPOINT)\s+(.*)", line, re.IGNORECASE)
            command = match.group(1) if match else ""
            if command.startswith("["):
                try:
                    command = " ".join(str(part) for part in json.loads(command))
                except ValueError:
                    pass
        for target in _command_targets(command, base, paths) if command else ():
            found.append((target, number, f"{path}: {'process' if name == 'Procfile' else 'container command'}", command))
    return found


def _html_scripts(path: str, text: str, paths: set[str]) -> list[tuple[str, int, str, str]]:
    found: list[tuple[str, int, str, str]] = []
    for match in _HTML_SCRIPT.finditer(text):
        source = match.group(1)
        if re.match(r"(?:[a-z][a-z0-9+.-]*:)?//", source, re.IGNORECASE):
            continue  # a CDN script, not part of this repository
        target = _resolve_file(source, posixpath.dirname(path), paths)
        if target:
            found.append((target, text.count("\n", 0, match.start()) + 1, f"{path}: script", source))
    return found


def _is_manifest_like(path: str) -> bool:
    base = posixpath.basename(path)
    return base in ("package.json", "pyproject.toml", "Procfile") or base.startswith("Dockerfile") or path.endswith(".html")


_NEXT_FILES = (
    "app/layout", "app/page", "pages/_app", "pages/index", "middleware", "next.config",
)
_NEXT_EXTENSIONS = (".tsx", ".ts", ".jsx", ".js", ".mjs")


def _next_entries(paths: set[str], manifest_texts: dict[str, str]) -> list[tuple[str, int, str, str]]:
    """Next.js has no `listen()` or `createRoot()` in the repository: the framework starts from conventional files.
    For each package that depends on `next` (or has a `next.config.*`), return the root layout, the home page, the
    pages-router app, the middleware and the config that exist, as (path, line, declared_by, detail)."""
    found: list[tuple[str, int, str, str]] = []
    for manifest, text in sorted(manifest_texts.items()):
        try:
            data = json.loads(text)
        except ValueError:
            continue
        base = posixpath.dirname(manifest)
        deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})} if isinstance(data, dict) else {}
        has_config = any(_join(base, f"next.config{ext}") in paths for ext in _NEXT_EXTENSIONS)
        if "next" not in deps and not has_config:
            continue
        line = _line_of(text, '"next"')
        for root in (base, _join(base, "src")):
            for stem in _NEXT_FILES:
                for ext in _NEXT_EXTENSIONS:
                    candidate = _join(root, stem + ext)
                    if candidate in paths:
                        found.append((candidate, line, f"{manifest}: dependencies.next", f"Next.js convention: {stem}"))
                        break
    return found


def discover(paths: set[str], contents: dict[str, str]) -> Discovery:
    """Entry points of one snapshot from `contents` (the manifests and the candidate source files that were loaded).

    Pure: the same files always give the same answer. Only files in `paths` can be reported."""
    out = Discovery()
    entries: dict[str, EntryPoint] = {}

    def entry(path: str, line: int, kind: str, area: str) -> EntryPoint:
        existing = entries.get(path)
        if existing is None:
            existing = entries[path] = EntryPoint(path, line, kind, area)
        return existing

    for path in sorted(contents):
        if _is_manifest_like(path):
            continue
        for hit in scan_entrypoints(path, contents[path]):
            found = entry(path, hit.line, hit.kind, hit.area)
            if not found.in_code or (found.kind in ("app_object", "script_main") and hit.kind in ("server_start", "ui_bootstrap")):
                found.kind, found.area, found.line = hit.kind, hit.area, hit.line
            found.in_code = True
            found.reasons.append(f"{hit.why} (line {hit.line}: {hit.text[:100]})")

    manifest_texts = {p: t for p, t in contents.items() if posixpath.basename(p) == "package.json" and not re.search(r"(?:^|/)node_modules/", p)}
    declared: list[tuple[str, int, str, str, str]] = []  # (target, line, declared_by, command, manifest path)
    for path in sorted(contents):
        text = contents[path]
        base = posixpath.basename(path)
        if re.search(r"(?:^|/)(?:node_modules|dist|build|vendor)/", path):
            continue
        if base == "package.json":
            items = _package_json(path, text, paths, manifest_texts, out)
        elif base == "pyproject.toml":
            items = _pyproject(path, text, paths)
        elif base == "Procfile" or base.startswith("Dockerfile"):
            items = _process_file(path, text, paths)
        elif path.endswith(".html"):
            items = _html_scripts(path, text, paths)
        else:
            continue
        for target, line, declared_by, command in items:
            declared.append((target, line, declared_by, command, declared_by.split(":")[0]))

    for target, line, declared_by, command in _next_entries(paths, manifest_texts):
        declared.append((target, line, declared_by, command, declared_by.split(":")[0]))

    seen_mentions: set[tuple[str, int]] = set()
    for target, line, declared_by, command, manifest in declared:
        mention = (manifest, line)
        if mention not in seen_mentions:
            seen_mentions.add(mention)
            out.declarations.append(DeclaredMention(manifest, line, f"{declared_by.split(': ', 1)[-1]} = {command[:100]}"))
        if not target:
            continue
        area = "frontend" if target.endswith((".jsx", ".tsx", ".vue", ".svelte")) or declared_by.endswith(": script") else "app"
        found = entry(target, 1, "declared", area)
        if declared_by not in found.declared_by:
            found.declared_by.append(declared_by)
        found.reasons.append(f"declared by {declared_by} ({command[:100]})")

    out.entries = sorted(entries.values(), key=lambda e: e.rank)
    out.discrepancies = list(dict.fromkeys(out.discrepancies))
    # Declared-only entries inherit an area from the construct when present; otherwise `app` (unknown).
    manifest_deps = parse_manifests({p: t for p, t in contents.items() if posixpath.basename(p) in ("package.json", "pyproject.toml") or re.match(r"requirements.*\.txt$", posixpath.basename(p))})
    out.technologies = [t.name for t in detect_technologies(manifest_deps, {})]
    return out


# --- selection and rendering (pure) ----------------------------------------------------------------------------


def select_entries(entries: list[EntryPoint], preference: str | None, limit: int) -> list[EntryPoint]:
    """The entries worth showing: best-ranked first, but alternating between areas (backend, frontend, ...) so a
    general question shows both ends. With a `preference`, that area comes first."""
    by_area: dict[str, list[EntryPoint]] = {}
    for item in entries:  # already ranked
        by_area.setdefault(item.area, []).append(item)
    order = sorted(by_area, key=lambda a: (a != preference, by_area[a][0].rank))
    chosen: list[EntryPoint] = []
    depth = 0
    while len(chosen) < limit and any(depth < len(v) for v in by_area.values()):
        for area in order:
            if depth < len(by_area[area]) and len(chosen) < limit:
                chosen.append(by_area[area][depth])
        depth += 1
    return chosen


def _one_line(value: str, limit: int = 140) -> str:
    return re.sub(r"\s+", " ", value).replace("<<<", "<").replace(">>>", ">").strip()[:limit]


def layout_lines(paths: list[str], max_lines: int = 14) -> list[str]:
    """A compact top-level layout: each directory with its file count and main subdirectories, then root files."""
    counts: Counter[str] = Counter()
    subdirs: dict[str, Counter[str]] = {}
    root_files: list[str] = []
    for path in paths:
        head, _, rest = path.partition("/")
        if not rest:
            root_files.append(path)
            continue
        counts[head] += 1
        sub, _, deeper = rest.partition("/")
        if deeper:
            subdirs.setdefault(head, Counter())[sub] += 1
    lines = []
    for directory, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:max_lines]:
        children = [name for name, _ in sorted(subdirs.get(directory, Counter()).items(), key=lambda kv: (-kv[1], kv[0]))[:6]]
        lines.append(f"{_one_line(directory)}/ ({count} files)" + (f": {', '.join(_one_line(c, 40) for c in children)}" if children else ""))
    if root_files:
        lines.append("root files: " + ", ".join(_one_line(f, 40) for f in sorted(root_files)[:12]) + (" ..." if len(root_files) > 12 else ""))
    return lines


def render_map(
    paths: list[str], discovery: Discovery, shown: list[EntryPoint], omitted: int, notes: list[str], max_chars: int = 3_500
) -> str:
    """The labelled, non-citable repository map placed beside the excerpts."""
    lines = ["Top-level layout:"] + [f"  {entry}" for entry in layout_lines(paths)]
    manifests = sorted(p for p in paths if posixpath.basename(p) in ("package.json", "pyproject.toml", "Procfile", "go.mod", "Cargo.toml") or posixpath.basename(p).startswith("Dockerfile"))
    if manifests:
        lines.append("Manifests: " + ", ".join(_one_line(m, 60) for m in manifests[:8]))
    if discovery.technologies:
        lines.append("Declared stack: " + ", ".join(discovery.technologies[:14]))
    if shown:
        lines.append("Entry points found (each backed by a construct in the code and/or a manifest declaration):")
        for item in shown[:8]:
            evidence = "; ".join(_one_line(r, 120) for r in item.reasons[:2])
            lines.append(f"  - {_one_line(item.path, 90)}:{item.line} [{item.area}] {evidence}")
        if omitted:
            lines.append(f"  ({omitted} more candidate(s) omitted)")
    uncertainty = [*discovery.discrepancies, *notes]
    if uncertainty:
        lines.append("Uncertainty and gaps:")
        lines += [f"  - {_one_line(n, 200)}" for n in uncertainty]
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[: max_chars - 20].rsplit("\n", 1)[0] + "\n  (map truncated)"


def gap_notes(discovery: Discovery, shown: list[EntryPoint], preference: str | None, scan_truncated: bool) -> list[str]:
    notes: list[str] = []
    areas = {e.area for e in discovery.entries}
    if not discovery.entries:
        notes.append("No server start, UI mount, script entry or declared start target was detected; the entry point could not be determined from this snapshot.")
    else:
        if preference == "frontend" and "frontend" not in areas:
            notes.append("No frontend bootstrap (UI mount) was detected.")
        if preference == "backend" and "backend" not in areas:
            notes.append("No backend server start was detected.")
        if not shown:
            notes.append("Entry-point candidates were found but none could be shown.")
    if scan_truncated:
        notes.append("The scan of source files for start-up constructs was capped, so some candidates may be missing.")
    return notes


# --- database glue ---------------------------------------------------------------------------------------------


@dataclass(slots=True)
class StructureContext:
    hits: list  # list[SearchHit]; typed loosely to avoid an import cycle with code_search
    map_text: str | None
    reference_paths: tuple[str, ...]
    warnings: list[str] = field(default_factory=list)


_CONFIG_BASENAMES = ("package.json", "pyproject.toml", "Procfile", "tsconfig.json", "jsconfig.json")


async def _load_contents(db: AsyncSession, repository_id: int, sha: str, wanted: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for start in range(0, len(wanted), 200):
        rows = await db.execute(
            select(SourceFile.path, SourceFile.content).where(
                SourceFile.repository_id == repository_id, SourceFile.commit_sha == sha, SourceFile.path.in_(wanted[start: start + 200])
            )
        )
        out.update({p: c for p, c in rows.all()})
    return out


async def structure_context(
    db: AsyncSession, repository_id: int, sha: str, question: str, *, settings: Settings | None = None
) -> StructureContext | None:
    """Discover entry points and structure for `question` against one repository snapshot and return the chunks
    to cite plus the map. None when the repository has no stored files."""
    from apps.api.services.code_search import SearchHit  # local import: code_search does not depend on this module

    settings = settings or get_settings()
    listing = (
        await db.execute(
            select(SourceFile.path, SourceFile.size_bytes).where(SourceFile.repository_id == repository_id, SourceFile.commit_sha == sha)
        )
    ).all()
    if not listing:
        return None
    sizes = {p: s for p, s in listing}
    paths = set(sizes)

    manifest_paths = sorted(
        p for p in paths
        if not re.search(r"(?:^|/)(?:node_modules|dist|build|vendor)/", p)
        and (posixpath.basename(p) in _CONFIG_BASENAMES or posixpath.basename(p).startswith("Dockerfile") or (p.endswith(".html") and sizes[p] <= 100_000))
        and sizes[p] <= 200_000
    )
    contents = await _load_contents(db, repository_id, sha, manifest_paths)

    markers = entry_markers()
    scan_limit = settings.rag_structure_scan_limit
    candidates = (
        await db.execute(
            select(SourceFile.path, SourceFile.content)
            .where(
                SourceFile.repository_id == repository_id, SourceFile.commit_sha == sha, SourceFile.size_bytes <= 400_000,
                or_(*[SourceFile.content.contains(m, autoescape=True) for m in markers]),
            )
            .order_by(SourceFile.path)
            .limit(scan_limit + 1)
        )
    ).all()
    scan_truncated = len(candidates) > scan_limit
    for path, content in candidates[:scan_limit]:
        if scannable_for_entrypoints(path):
            contents[path] = content

    discovery = discover(paths, contents)
    missing = [e.path for e in discovery.entries if e.path not in contents]
    contents.update(await _load_contents(db, repository_id, sha, missing))  # declared targets need their text for lines and imports

    preference = area_preference(question)
    pool = discovery.entries
    if preference and any(e.area == preference for e in pool):
        pool = [e for e in pool if e.area == preference]  # asked about one end: do not spend the budget on the other
    # Leave room in the budget for the manifest that declares the start target and for one local dependency.
    shown = select_entries(pool, preference, max(2, settings.rag_structure_chunks - 2))
    omitted = max(0, len(discovery.entries) - len(shown))

    # One hop from the best two code-confirmed entries: the local modules they load.
    dependencies = _dependencies([e for e in shown if e.in_code][:2], paths, contents)

    wanted_lines: dict[str, list[tuple[int, str]]] = {}  # path -> [(line, why)]
    for item in shown:
        wanted_lines.setdefault(item.path, []).append((item.line, item.reasons[0] if item.reasons else item.kind))
    used_manifests = {declared.split(":")[0] for item in shown for declared in item.declared_by}
    for mention in [d for d in discovery.declarations if d.path in used_manifests][:3]:  # only manifests behind a shown entry
        if mention.path in paths:
            wanted_lines.setdefault(mention.path, []).append((mention.line, f"manifest: {mention.detail}"))
    for target, importer in dependencies:
        wanted_lines.setdefault(target, []).append((1, f"imported by {importer}"))

    hits = (await _chunks_for(db, repository_id, sha, wanted_lines, SearchHit, settings))[: settings.rag_structure_chunks]
    notes = gap_notes(discovery, shown, preference, scan_truncated)
    map_text = render_map(sorted(paths), discovery, shown, omitted, notes)
    warnings = [n for n in notes if n.startswith("No ")]
    return StructureContext(hits=hits, map_text=map_text, reference_paths=tuple(sorted(paths))[:2_000], warnings=warnings)


def _dependencies(entries: list[EntryPoint], paths: set[str], contents: dict[str, str]) -> list[tuple[str, str]]:
    """(imported file, importing entry) for up to two local modules per entry, in import order."""
    code = {p for p in paths if language_for_path(p)}
    config = {p: t for p, t in contents.items() if posixpath.basename(p) in ("tsconfig.json", "jsconfig.json", "package.json")}
    try:
        resolver = Resolver(paths, code, config)
    except Exception:  # a malformed config must not cost the answer its entry points
        logger.warning("Import resolver could not be built for structure retrieval", exc_info=True)
        return []
    extractor = ImportExtractor()
    found: list[tuple[str, str]] = []
    for item in entries:
        language = language_for_path(item.path)
        text = contents.get(item.path)
        if not language or text is None:
            continue
        try:
            imports = extractor.extract(text, language).imports
        except ExtractionError:
            continue
        taken = 0
        for raw in imports:
            outcome = resolver.resolve(item.path, raw)
            if outcome.status != "internal":
                continue
            for target in outcome.targets:
                if target.path in code and target.path != item.path and target.path not in {t for t, _ in found} and taken < 2:
                    found.append((target.path, item.path))
                    taken += 1
    return found


async def _chunks_for(db, repository_id: int, sha: str, wanted: dict[str, list[tuple[int, str]]], hit_type, settings: Settings) -> list:
    """The stored chunk containing each wanted line, as search hits that carry the reason they were chosen."""
    rows = (
        await db.execute(
            select(CodeChunk, SourceFile.path)
            .join(SourceFile, SourceFile.id == CodeChunk.source_file_id)
            .where(SourceFile.repository_id == repository_id, SourceFile.commit_sha == sha, SourceFile.path.in_(list(wanted)))
            .order_by(SourceFile.path, CodeChunk.chunk_index)
        )
    ).all()
    chunks: dict[str, list[CodeChunk]] = {}
    for chunk, path in rows:
        chunks.setdefault(path, []).append(chunk)
    hits: list = []
    seen: set[int] = set()
    total = len(wanted)
    for position, (path, targets) in enumerate(wanted.items()):  # insertion order = priority order
        for line, why in targets:
            candidates = chunks.get(path) or []
            chunk = next((c for c in candidates if c.start_line <= line <= c.end_line), candidates[0] if candidates else None)
            if chunk is None or chunk.id in seen:
                continue
            seen.add(chunk.id)
            hits.append(hit_type(
                chunk_id=chunk.id, repository_id=repository_id, commit_sha=sha, source_file_id=chunk.source_file_id, path=path,
                language=chunk.language, kind=chunk.kind, symbol_name=chunk.symbol_name, symbols=list(chunk.symbols or []),
                parser_mode=chunk.parser_mode, start_line=chunk.start_line, end_line=chunk.end_line, start_byte=chunk.start_byte,
                end_byte=chunk.end_byte, content=chunk.content, score=1.0 - position / (total + 1), keyword_rank=None,
                semantic_rank=None, structure_reason=_one_line(why, 160),
            ))
    return hits
