"""Resolve an import specifier to a file in the same snapshot, an external package, or "unresolved".

Resolution uses only the repository's own file list and config files (tsconfig/jsconfig `paths` and
`baseUrl`, package.json workspace names, Python package layout). It is not compiler-level symbol
resolution: it answers "which file does this import statement name?", nothing about which symbol is used.

Every outcome is explicit, so a graph never gets an edge it cannot justify:

* `internal`   - one or more files in the snapshot (each with how it was found and a confidence);
* `external`   - a package, standard-library or URL import: not a node, listed separately;
* `asset`      - the file exists but is not source code (css, json, svg...): no edge;
* `namespace`  - a Python namespace directory without an `__init__.py`: nothing to point at;
* `unresolved` - the import names something that is not there (or an alias nothing configures).
"""

from __future__ import annotations

import json
import posixpath
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass

from apps.api.services.architecture.extract import RawImport

VERIFIED = "verified"
HEURISTIC = "heuristic"

PY_STDLIB = frozenset(sys.stdlib_module_names)
NODE_BUILTINS = frozenset(
    "assert async_hooks buffer child_process cluster console constants crypto dgram diagnostics_channel dns domain "
    "events fs http http2 https inspector module net os path perf_hooks process punycode querystring readline repl "
    "stream string_decoder sys timers tls trace_events tty url util v8 vm wasi worker_threads zlib".split()
)
_JS_EXTS_TS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".d.ts")
_JS_EXTS_JS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".d.ts")
_JS_EXT_SWAP = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}
_SCHEME = re.compile(r"^([a-z][a-z0-9+.-]*):", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Target:
    path: str
    how: str  # relative | absolute | submodule | ancestor_dir | alias | base_url | workspace
    confidence: str


@dataclass(frozen=True, slots=True)
class Resolution:
    status: str  # internal | external | asset | namespace | unresolved | self
    targets: tuple[Target, ...] = ()
    package: str | None = None
    ecosystem: str | None = None  # python | python_stdlib | npm | node_builtin | url
    reason: str | None = None  # unresolved: not_found | alias_not_configured | relative_beyond_root | unsupported


def loads_jsonc(text: str) -> dict:
    """Parse JSON with comments and trailing commas (tsconfig style). Returns {} when it still is not JSON."""
    out: list[str] = []
    i, n, in_string = 0, len(text), False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
            out.append(ch)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 1
        else:
            out.append(ch)
        i += 1
    cleaned = re.sub(r",(\s*[}\]])", r"\1", "".join(out))
    try:
        data = json.loads(cleaned)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


@dataclass(slots=True)
class TsConfig:
    directory: str
    base_url: str | None  # repository-relative directory
    paths: list[tuple[str, list[str]]]
    paths_base: str


def _dirname(path: str) -> str:
    return posixpath.dirname(path)


def _join(*parts: str) -> str:
    joined = posixpath.normpath(posixpath.join(*parts)) if any(parts) else ""
    return "" if joined == "." else joined


def _package_name(spec: str) -> str:
    parts = spec.split("/")
    return "/".join(parts[:2]) if spec.startswith("@") and len(parts) > 1 else parts[0]


class Resolver:
    def __init__(self, all_paths: Iterable[str], code_paths: set[str], texts: dict[str, str]) -> None:
        """`texts` holds the content of config files (package.json, tsconfig/jsconfig) and Python `__init__.py`s."""
        self._files = set(all_paths)
        self._code = code_paths
        self._texts = texts
        self._dirs = {""}
        for path in self._files:
            directory = _dirname(path)
            while directory and directory not in self._dirs:
                self._dirs.add(directory)
                directory = _dirname(directory)
        self._py_roots = self._detect_python_roots()
        self._tsconfigs: dict[str, TsConfig | None] = {}
        self._workspaces = self._load_workspaces()

    # --- project layout -----------------------------------------------------------------------------------

    def _detect_python_roots(self) -> list[str]:
        """Import roots: the repository root, plus the parent of every top-most package (`src/pkg/__init__.py` -> `src`)."""
        roots = {""}
        for path in self._code:
            if path.endswith("__init__.py") and posixpath.basename(path) == "__init__.py":
                package = _dirname(path)
                parent = _dirname(package)
                if package and (not parent or _join(parent, "__init__.py") not in self._code):
                    roots.add(parent)
        return [""] + sorted(roots - {""})

    def _load_workspaces(self) -> dict[str, str]:
        found: dict[str, str] = {}
        for path, text in self._texts.items():
            if posixpath.basename(path) == "package.json":
                try:
                    name = json.loads(text).get("name")
                except (ValueError, AttributeError):
                    continue
                if isinstance(name, str) and name:
                    found.setdefault(name, _dirname(path))
        return found

    def _config_at(self, directory: str, depth: int = 0) -> TsConfig | None:
        for name in ("tsconfig.json", "jsconfig.json"):
            path = _join(directory, name)
            if path in self._texts:
                return self._load_config(path, depth)
        return None

    def _load_config(self, path: str, depth: int = 0) -> TsConfig | None:
        data = loads_jsonc(self._texts.get(path, ""))
        if not data:
            return None
        directory = _dirname(path)
        base_url: str | None = None
        paths: list[tuple[str, list[str]]] = []
        paths_dir = directory
        parent_ref = data.get("extends")
        if isinstance(parent_ref, str) and parent_ref.startswith(".") and depth < 5:
            target = _join(directory, parent_ref)
            parent = self._load_config(target if target in self._texts else target + ".json", depth + 1)
            if parent:
                base_url, paths, paths_dir = parent.base_url, list(parent.paths), parent.paths_base
        options = data.get("compilerOptions") or {}
        if isinstance(options.get("baseUrl"), str):
            base_url = _join(directory, options["baseUrl"])
        if isinstance(options.get("paths"), dict):
            paths = [(k, [t for t in v if isinstance(t, str)]) for k, v in options["paths"].items() if isinstance(v, list)]
            paths_dir = directory
        return TsConfig(directory, base_url, paths, base_url if base_url is not None else paths_dir)

    def _nearest_config(self, importer: str) -> TsConfig | None:
        directory = _dirname(importer)
        while True:
            if directory not in self._tsconfigs:
                self._tsconfigs[directory] = self._config_at(directory)
            if self._tsconfigs[directory]:
                return self._tsconfigs[directory]
            if not directory:
                return None
            directory = _dirname(directory)

    # --- entry point --------------------------------------------------------------------------------------

    def resolve(self, importer: str, raw: RawImport) -> Resolution:
        if raw.language == "python":
            return self._resolve_python(importer, raw.specifier, raw.names)
        return self._resolve_js(importer, raw.specifier)

    # --- Python -------------------------------------------------------------------------------------------

    def _find_module(self, parts: list[str]) -> tuple[str | None, bool]:
        """(file, is_directory) for a module path: `a/b.py` or `a/b/__init__.py`; the directory flag marks a namespace package."""
        base = "/".join(parts)
        candidates = (f"{base}.py", f"{base}/__init__.py", f"{base}.pyi") if base else ("__init__.py",)
        for candidate in candidates:
            if candidate in self._code:
                return candidate, False
        return None, base in self._dirs

    def _py_targets(self, parts: list[str], names: tuple[str, ...], how: str, confidence: str) -> tuple[list[Target], bool]:
        """Files an import names.

        `from pkg import a, b` depends on the submodules `a` and `b`; the package's own `__init__.py` is left out when
        every name is such a submodule (it adds an edge to usually-empty glue, not information). If `__init__.py`
        mentions a name, the name may be an attribute defined there, so it is kept and the submodule edge is a guess.
        """
        module, is_dir = self._find_module(parts)
        targets: list[Target] = []
        if module:
            targets.append(Target(module, how, confidence))
        wanted = [n for n in names if n and n != "*"]
        submodules: list[Target] = []
        for name in wanted:
            sub, _ = self._find_module(parts + name.split("."))
            if sub and sub != module and all(t.path != sub for t in targets + submodules):
                shadowed = bool(module and module.endswith("__init__.py") and re.search(rf"\b{re.escape(name)}\b", self._texts.get(module, "")))
                submodules.append(Target(sub, "submodule", HEURISTIC if shadowed else confidence))
        if module and wanted and len(submodules) == len(wanted) and all(t.confidence == VERIFIED for t in submodules):
            targets = []
        targets += submodules
        return targets, not targets and is_dir

    def _resolve_python(self, importer: str, spec: str, names: tuple[str, ...]) -> Resolution:
        level = len(spec) - len(spec.lstrip("."))
        module_parts = [p for p in spec[level:].split(".") if p]
        importer_dir = [p for p in _dirname(importer).split("/") if p]
        if level:
            up = level - 1
            if up > len(importer_dir):
                return Resolution("unresolved", reason="relative_beyond_root")
            base = importer_dir[: len(importer_dir) - up]
            targets, namespace = self._py_targets(base + module_parts, names, "relative", VERIFIED)
            return self._finish(importer, targets, namespace, Resolution("unresolved", reason="not_found"))
        if not module_parts:
            return Resolution("unresolved", reason="unsupported")
        first = module_parts[0]
        for root in self._py_roots:
            prefix = [p for p in root.split("/") if p]
            targets, namespace = self._py_targets(prefix + module_parts, names, "absolute", VERIFIED)
            if targets or namespace:
                return self._finish(importer, targets, namespace, Resolution("unresolved", reason="not_found"))
        if first not in PY_STDLIB:
            # Scripts and test runners often put the importer's own directory or an ancestor of it on sys.path
            # (pytest's `tests/`, a script's folder). That is a guess about the runtime, so it is only a heuristic
            # edge, never tried for standard-library names, and only when the file really exists there.
            for depth in range(len(importer_dir), 0, -1):
                targets, namespace = self._py_targets(importer_dir[:depth] + module_parts, names, "ancestor_dir", HEURISTIC)
                if targets:
                    return self._finish(importer, targets, namespace, Resolution("unresolved", reason="not_found"))
        if first in PY_STDLIB:
            return Resolution("external", package=first, ecosystem="python_stdlib")
        if any(self._find_module([p for p in root.split("/") if p] + [first]) != (None, False) for root in self._py_roots):
            return Resolution("unresolved", reason="not_found")  # a local package, but the named submodule is missing
        return Resolution("external", package=first, ecosystem="python")

    @staticmethod
    def _finish(importer: str, targets: list[Target], namespace: bool, missing: Resolution) -> Resolution:
        others = [t for t in targets if t.path != importer]
        if others:
            return Resolution("internal", tuple(others))
        if targets:
            return Resolution("self")
        return Resolution("namespace") if namespace else missing

    # --- JavaScript / TypeScript --------------------------------------------------------------------------

    def _try_file(self, base: str, importer_language: str) -> tuple[str, str] | None:
        """(kind, path) for the file `base` names under Node/TypeScript resolution, kind being "code" or "asset"."""
        if base in self._files:
            return ("code" if base in self._code else "asset", base)
        exts = _JS_EXTS_TS if importer_language in ("typescript", "tsx") else _JS_EXTS_JS
        stem, ext = posixpath.splitext(base)
        for swap in _JS_EXT_SWAP.get(ext, ()):
            if stem + swap in self._code:
                return "code", stem + swap
        for suffix in exts:
            if base + suffix in self._code:
                return "code", base + suffix
        for suffix in exts:
            if _join(base, "index" + suffix) in self._code:
                return "code", _join(base, "index" + suffix)
        for suffix in (".json", ".css", ".scss", ".svg"):
            if base + suffix in self._files:
                return "asset", base + suffix
        return None

    def _js_hit(self, importer: str, hit: tuple[str, str] | None, how: str, confidence: str = VERIFIED) -> Resolution | None:
        if hit is None:
            return None
        kind, path = hit
        if kind == "asset":
            return Resolution("asset")
        if path == importer:
            return Resolution("self")
        return Resolution("internal", (Target(path, how, confidence),))

    def _resolve_js(self, importer: str, raw_spec: str) -> Resolution:
        language = "tsx" if importer.endswith((".tsx", ".jsx")) else ("typescript" if importer.endswith((".ts", ".mts", ".cts")) else "javascript")
        spec = re.split(r"[?#]", raw_spec, maxsplit=1)[0]
        if not spec:
            return Resolution("unresolved", reason="unsupported")
        if spec in (".", "..") or spec.startswith(("./", "../")):
            hit = self._js_hit(importer, self._try_file(_join(_dirname(importer), spec), language), "relative")
            return hit or Resolution("unresolved", reason="not_found")
        if spec.startswith("/"):
            return Resolution("unresolved", reason="unsupported")
        scheme = _SCHEME.match(spec)
        if scheme:
            return self._resolve_scheme(spec, scheme.group(1).lower())
        if spec.split("/")[0] in NODE_BUILTINS:
            return Resolution("external", package=spec.split("/")[0], ecosystem="node_builtin")
        config = self._nearest_config(importer)
        if config:
            matched = self._alias_match(config, spec)
            if matched:
                for candidate in matched[0]:
                    found = self._js_hit(importer, self._try_file(candidate, language), "alias")
                    if found:
                        return found
                if matched[1] != "*":  # a catch-all like "*" is only a fallback; a real alias with no file is an error
                    return Resolution("unresolved", reason="not_found")
            if config.base_url is not None:
                found = self._js_hit(importer, self._try_file(_join(config.base_url, spec), language), "base_url")
                if found:
                    return found
        workspace = self._resolve_workspace(importer, spec, language)
        if workspace:
            return workspace
        if spec.startswith(("@/", "~/", "#")) or spec in ("@", "~"):
            return Resolution("unresolved", reason="alias_not_configured")
        return Resolution("external", package=_package_name(spec), ecosystem="npm")

    @staticmethod
    def _resolve_scheme(spec: str, scheme: str) -> Resolution:
        rest = spec[len(scheme) + 1:]
        if scheme == "node":
            return Resolution("external", package=rest.split("/")[0], ecosystem="node_builtin")
        if scheme in ("npm", "jsr"):
            parts = rest.lstrip("/").split("/")
            if parts[0].startswith("@") and len(parts) > 1:
                name = f"{parts[0]}/{parts[1].split('@')[0]}"
            else:
                name = parts[0].split("@")[0]
            return Resolution("external", package=name if scheme == "npm" else f"jsr:{name}", ecosystem="npm" if scheme == "npm" else "url")
        if scheme in ("http", "https"):
            location = rest.lstrip("/").split("?")[0]
            host, _, path = location.partition("/")
            first = re.sub(r"@[^/]*$", "", path.split("/")[0]) if path else ""
            return Resolution("external", package=f"{host}/{first}".rstrip("/"), ecosystem="url")
        return Resolution("external", package=spec, ecosystem="url")

    @staticmethod
    def _alias_match(config: TsConfig, spec: str) -> tuple[list[str], str] | None:
        best: tuple[int, list[str], str] | None = None
        for pattern, targets in config.paths:
            if "*" in pattern:
                prefix, _, suffix = pattern.partition("*")
                if spec.startswith(prefix) and spec.endswith(suffix) and len(spec) >= len(prefix) + len(suffix):
                    star = spec[len(prefix): len(spec) - len(suffix)]
                    score = len(prefix)
                else:
                    continue
            elif pattern == spec:
                star, score = "", 10**6
            else:
                continue
            candidates = [_join(config.paths_base, t.replace("*", star)) for t in targets]
            if best is None or score > best[0]:
                best = (score, candidates, pattern)
        return (best[1], best[2]) if best else None

    def _resolve_workspace(self, importer: str, spec: str, language: str) -> Resolution | None:
        for name, directory in self._workspaces.items():
            if spec != name and not spec.startswith(name + "/"):
                continue
            rest = spec[len(name) + 1:]
            if rest:
                hit = self._try_file(_join(directory, rest), language) or self._try_file(_join(directory, "src", rest), language)
                found = self._js_hit(importer, hit, "workspace")
                if found:
                    return found
                return Resolution("unresolved", reason="not_found")
            entry = self._package_entry(directory, language)
            found = self._js_hit(importer, entry[0], "workspace", entry[1]) if entry else None
            return found or Resolution("unresolved", reason="not_found")
        return None

    def _package_entry(self, directory: str, language: str) -> tuple[tuple[str, str] | None, str] | None:
        try:
            manifest = json.loads(self._texts.get(_join(directory, "package.json"), "{}"))
        except ValueError:
            manifest = {}
        declared = [manifest.get(key) for key in ("source", "module", "main")]
        exports = manifest.get("exports")
        if isinstance(exports, str):
            declared.insert(0, exports)
        elif isinstance(exports, dict):
            root = exports.get(".", exports)
            if isinstance(root, str):
                declared.insert(0, root)
            elif isinstance(root, dict):
                declared[:0] = [root.get(k) for k in ("import", "default", "require") if isinstance(root.get(k), str)]
        for entry in declared:
            if isinstance(entry, str):
                hit = self._try_file(_join(directory, entry), language)
                if hit and hit[0] == "code":
                    return hit, VERIFIED
        for guess in ("src/index", "index"):
            hit = self._try_file(_join(directory, guess), language)
            if hit and hit[0] == "code":
                return hit, HEURISTIC
        return None
