"""Build the architecture graph of one snapshot: pure and deterministic, no database and no AI provider.

Pipeline: pick the supported code files -> extract imports (tree-sitter) -> resolve each to a file, a
package or "unresolved" -> file-level graph -> detect roles/areas/technologies with evidence -> group files
into modules -> aggregate module-level edges -> find cycles. The same input always yields the same output.
"""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict
from dataclasses import dataclass

from apps.api.schemas.architecture import (
    Area, Cycle, Evidence, ExternalDependency, FileExternal, Graph, GraphEdge, GraphNode, NodeMetrics, Stats,
    UnresolvedImport,
)
from apps.api.services.architecture.extract import IMPORTS, LAZY, REEXPORTS, REQUIRES, TYPE_ONLY, ExtractionError, ImportExtractor
from apps.api.services.architecture.resolve import HEURISTIC, VERIFIED, Resolver
from apps.api.services.architecture.signals import (
    AREA_LABEL, ROLE_AREA, ROLE_ORDER, RoleHit, Signals, detect_technologies, parse_manifests,
)
from apps.api.services.code_parser import language_for_path

ANALYZER_VERSION = 1

EAGER = {IMPORTS, REEXPORTS, REQUIRES}  # relationships that run at import time; lazy and type-only ones cannot form a load-time cycle
MAX_EDGE_EVIDENCE = 3
MAX_MODULE_EDGE_EVIDENCE = 3
MAX_MODULE_FILES = 500
MAX_AREA_FILES = 50
MAX_AREA_EVIDENCE = 8
MAX_UNRESOLVED_STORED = 1_000
MAX_CYCLE_LISTING = 50  # nodes listed per cycle in the overview (the full membership is each node's cycle_id)
MAX_CYCLES_LISTED = 100
MAX_EXTERNALS_IN_OVERVIEW = 100
MODULE_TARGET_MIN = 6  # split large modules until there are at least this many (when they are big enough to split)
MODULE_MAX_FILES = 40  # modules bigger than this are split by sub-directory

MAX_JSON_BYTES = 100_000
CONFIG_NAMES = {"package.json", "tsconfig.json", "jsconfig.json", "pyproject.toml", "setup.py"}
_DDL = re.compile(r"^\s*(?:create|alter)\s+(?:or\s+replace\s+)?(?:table|type|policy|index|function|extension)\b", re.I | re.M)
_FUNCTIONS_TABLE = re.compile(r"^\s*\[functions\.([\w-]+)\]", re.M)


@dataclass(slots=True)
class SnapshotFile:
    path: str
    category: str
    size_bytes: int
    content: str | None  # loaded only for source code, manifests/config and migration-like files


@dataclass(slots=True)
class AnalysisResult:
    graph: Graph
    detail: dict  # file-level nodes, edges, externals, unresolved imports and cycles (JSON-ready)


def wanted_content(path: str, size_bytes: int) -> bool:
    """Which files' text the analysis needs: supported source files, manifests/config, and DDL-bearing SQL."""
    base = posixpath.basename(path)
    if language_for_path(path) or base in CONFIG_NAMES or re.match(r"requirements.*\.txt$", base) or path.endswith("supabase/config.toml"):
        return True
    if base in ("alembic.ini", "schema.prisma"):
        return True
    if path.endswith(".json") and size_bytes <= MAX_JSON_BYTES:  # small JSON may be a tsconfig that another one `extends`
        return True
    return path.endswith(".sql") and size_bytes <= 200_000


# --- graph helpers ---------------------------------------------------------------------------------------------


def strongly_connected(adjacency: dict[str, set[str]]) -> list[list[str]]:
    """Components of two or more nodes (Tarjan, iterative so deep graphs cannot overflow the stack)."""
    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[list[str]] = []
    counter = 0
    for root in sorted(adjacency):
        if root in index_of:
            continue
        work = [(root, iter(sorted(adjacency.get(root, ()))))]
        index_of[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index_of:
                    index_of[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(adjacency.get(child, ())))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index_of[child])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index_of[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    components.append(sorted(component))
    return sorted(components)


def group_modules(paths: list[str], forced_roots: set[str]) -> dict[str, str]:
    """Assign every file to a module directory ("" is the repository root).

    Files start in their deepest forced root (a package.json/pyproject directory or a Supabase function) or else
    their top-level directory. A group is then split by sub-directory while it is larger than MODULE_MAX_FILES, or
    while there are fewer than MODULE_TARGET_MIN groups and it is big enough to be worth splitting. Files directly
    inside a split directory stay in it. The result depends only on the file list.
    """
    def start(path: str) -> str:
        directory = posixpath.dirname(path)
        best = ""
        for root in forced_roots:
            if (directory == root or directory.startswith(root + "/")) and len(root) > len(best):
                best = root
        return best or (path.split("/")[0] if "/" in path else "")

    groups: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        groups[start(path)].append(path)

    def splittable(group: str, files: list[str]) -> bool:
        return any(posixpath.dirname(f) != group for f in files)

    for _ in range(500):
        candidates = [(g, f) for g, f in groups.items() if splittable(g, f)]
        if not candidates:
            break
        group, files = max(candidates, key=lambda gf: (len(gf[1]), gf[0]))
        if not (len(files) > MODULE_MAX_FILES or (len(groups) < MODULE_TARGET_MIN and len(files) >= 6)):
            break
        del groups[group]
        for file in files:
            directory = posixpath.dirname(file)
            if directory == group:
                groups[group].append(file)
            else:
                rest = directory[len(group) + 1:] if group else directory
                groups[f"{group}/{rest.split('/')[0]}" if group else rest.split("/")[0]].append(file)
    return {file: group for group, files in groups.items() for file in files}


def _line_at(content: str, offset: int) -> str:
    end = content.find("\n", offset)
    return content[offset: len(content) if end == -1 else end].strip()[:120]


def _module_id(directory: str) -> str:
    return f"module:{directory or '.'}"


# --- the analysis ----------------------------------------------------------------------------------------------------


@dataclass(slots=True)
class _FileEdge:
    source: str
    target: str
    relationship: str
    how: str
    verified: bool
    count: int = 0
    evidence: list[Evidence] | None = None


def analyze_snapshot(commit_sha: str, files: list[SnapshotFile], *, max_files: int = 5_000) -> AnalysisResult:
    all_paths = {f.path for f in files}
    candidates = sorted((f for f in files if language_for_path(f.path) and f.content is not None), key=lambda f: f.path)
    missing_text = sum(1 for f in files if language_for_path(f.path) and f.content is None)
    selected, over_limit = candidates[:max_files], len(candidates) - min(len(candidates), max_files)
    warnings: list[str] = []
    if over_limit:
        warnings.append(f"Analysis is limited to {max_files} code files; {over_limit} more were skipped")
    code = {f.path for f in selected}
    languages: dict[str, int] = defaultdict(int)

    texts = {f.path: f.content for f in files if f.content is not None and (f.path.endswith((".json", "__init__.py")))}
    resolver = Resolver(all_paths, code, texts)
    extractor = ImportExtractor()

    edges: dict[tuple[str, str, str], _FileEdge] = {}
    external_by_file: dict[tuple[str, str, str], FileExternal] = {}
    package_importers: dict[tuple[str, str], dict[str, Evidence]] = defaultdict(dict)  # (ecosystem, name) -> {file: first import evidence}
    unresolved: list[UnresolvedImport] = []
    unresolved_total = non_code = dynamic_skipped = syntax_error_files = 0
    unparsed: list[str] = []
    per_file_externals: dict[str, dict[str, Evidence]] = defaultdict(dict)
    file_unresolved: dict[str, int] = defaultdict(int)

    for f in selected:
        language = language_for_path(f.path)
        assert language is not None and f.content is not None
        languages[language] += 1
        try:
            extraction = extractor.extract(f.content, language)
        except ExtractionError as exc:
            unparsed.append(f"{f.path} ({exc})")
            continue
        dynamic_skipped += extraction.dynamic_skipped
        syntax_error_files += extraction.syntax_errors
        for raw in extraction.imports:
            evidence = Evidence(path=f.path, start_line=raw.start_line, end_line=raw.end_line, kind={
                IMPORTS: "import", REEXPORTS: "reexport", REQUIRES: "require", LAZY: "lazy_import", TYPE_ONLY: "import",
            }[raw.relationship], detail=raw.text)
            outcome = resolver.resolve(f.path, raw)
            if outcome.status == "internal":
                for target in outcome.targets:
                    edge = edges.setdefault(
                        (f.path, target.path, raw.relationship),
                        _FileEdge(f.path, target.path, raw.relationship, target.how, True, 0, []),
                    )
                    edge.count += 1
                    edge.verified = edge.verified and target.confidence == VERIFIED
                    if len(edge.evidence or []) < MAX_EDGE_EVIDENCE:
                        edge.evidence.append(evidence)  # type: ignore[union-attr]
            elif outcome.status == "external":
                assert outcome.package and outcome.ecosystem
                package_importers[(outcome.ecosystem, outcome.package)].setdefault(f.path, evidence)
                key = (f.path, outcome.ecosystem, outcome.package)
                external_by_file.setdefault(key, FileExternal(file=f.path, name=outcome.package, ecosystem=outcome.ecosystem, evidence=evidence))
                per_file_externals[f.path].setdefault(outcome.package, evidence)
                if outcome.ecosystem == "node_builtin":
                    per_file_externals[f.path].setdefault(f"node:{outcome.package}", evidence)
            elif outcome.status == "asset":
                non_code += 1
            elif outcome.status == "unresolved":
                unresolved_total += 1
                file_unresolved[f.path] += 1
                if len(unresolved) < MAX_UNRESOLVED_STORED:
                    unresolved.append(UnresolvedImport(file=f.path, specifier=raw.specifier, reason=outcome.reason or "not_found", evidence=evidence))
    if unparsed:
        warnings.append(f"{len(unparsed)} file(s) could not be parsed and have no outgoing edges: " + ", ".join(unparsed[:3]) + (" ..." if len(unparsed) > 3 else ""))
    if syntax_error_files:
        warnings.append(f"{syntax_error_files} file(s) contain syntax errors; their imports are best-effort")
    if dynamic_skipped:
        warnings.append(f"{dynamic_skipped} require()/import() call(s) with a computed argument were not followed")
    if missing_text:
        warnings.append(f"{missing_text} code file(s) had no stored text and were skipped")

    # --- manifests, roles ------------------------------------------------------------------------------------
    manifest_texts = {f.path: f.content for f in files if f.content is not None and (posixpath.basename(f.path) in CONFIG_NAMES or re.match(r"requirements.*\.txt$|.*requirements/.+\.txt$", f.path))}
    deps = parse_manifests(manifest_texts)
    declared: dict[str, dict[str, Evidence]] = defaultdict(dict)
    for dep in deps:
        if dep.ecosystem == "npm":
            declared[dep.directory].setdefault(dep.name, Evidence(path=dep.path, start_line=dep.line, end_line=dep.line, kind="manifest", detail=f"declares dependency {dep.name}"))
    supabase: dict[str, Evidence] = {}
    extra_database: list[Evidence] = []
    for f in files:
        if f.content is None:
            continue
        if f.path.endswith("supabase/config.toml"):
            for match in _FUNCTIONS_TABLE.finditer(f.content):
                line = f.content.count("\n", 0, match.start()) + 1
                prefix = posixpath.dirname(f.path)
                supabase[f"{prefix}/functions/{match.group(1)}" if prefix else f"functions/{match.group(1)}"] = Evidence(
                    path=f.path, start_line=line, end_line=line, kind="manifest", detail=f"configures function {match.group(1)}"
                )
        elif f.path.endswith(".sql"):
            found = _DDL.search(f.content)
            if found:
                line = f.content.count("\n", 0, found.start()) + 1
                extra_database.append(Evidence(path=f.path, start_line=line, end_line=line, kind="code", detail="SQL schema: " + _line_at(f.content, found.start())))
        elif posixpath.basename(f.path) == "schema.prisma":
            model = re.search(r"^model\s+\w+\s*\{", f.content, re.M)
            if model:
                line = f.content.count("\n", 0, model.start()) + 1
                extra_database.append(Evidence(path=f.path, start_line=line, end_line=line, kind="code", detail="Prisma data model: " + _line_at(f.content, model.start())))
    signals = Signals(declared, supabase)

    roles: dict[str, list[RoleHit]] = {}
    for f in selected:
        language = language_for_path(f.path)
        found = signals.roles(f.path, language or "", f.content or "", per_file_externals.get(f.path, {}))  # type: ignore[arg-type]
        if found:
            roles[f.path] = found

    # --- file nodes and edges -----------------------------------------------------------------------------------
    file_edges: list[GraphEdge] = []
    for edge in edges.values():
        file_edges.append(GraphEdge(
            id=f"edge:file:{edge.source}->{edge.target}:{edge.relationship}", source=f"file:{edge.source}", target=f"file:{edge.target}",
            level="file", relationship=edge.relationship, basis="static", resolution=edge.how,
            confidence=VERIFIED if edge.verified else HEURISTIC, count=edge.count, evidence=edge.evidence or [],
            evidence_truncated=edge.count > len(edge.evidence or []),
        ))
    file_edges.sort(key=lambda e: e.id)

    out_n: dict[str, set[str]] = defaultdict(set)
    in_n: dict[str, set[str]] = defaultdict(set)
    for e in file_edges:
        out_n[e.source].add(e.target)
        in_n[e.target].add(e.source)
    eager_adj: dict[str, set[str]] = defaultdict(set)
    for e in file_edges:
        if e.relationship in EAGER:
            eager_adj[e.source].add(e.target)
    file_cycles = strongly_connected(dict(eager_adj))
    file_component = {n: f"cycle:file:{i}" for i, c in enumerate(file_cycles, 1) for n in c}
    for e in file_edges:  # an eager edge inside a strongly connected component lies on a cycle
        if e.relationship in EAGER and e.source in file_component and file_component[e.source] == file_component.get(e.target):
            e.cycle_id = file_component[e.source]

    file_nodes: dict[str, GraphNode] = {}
    for f in selected:
        node_id = f"file:{f.path}"
        hits = roles.get(f.path, [])
        evidence: list[Evidence] = [e for h in hits for e in h.evidence][:MAX_AREA_EVIDENCE]
        file_nodes[node_id] = GraphNode(
            id=node_id, label=posixpath.basename(f.path), type="file", kind=hits[0].role if hits else "source", path=f.path,
            language=language_for_path(f.path), files=[f.path], file_count=1, roles=[h.role for h in hits],
            area_ids=sorted({f"area:{ROLE_AREA[h.role]}" for h in hits if h.role in ROLE_AREA}), evidence=evidence,
            metrics=NodeMetrics(incoming=len(in_n.get(node_id, ())), outgoing=len(out_n.get(node_id, ())),
                                external_packages=len({n for n in per_file_externals.get(f.path, {}) if not n.startswith("node:")}),
                                unresolved_imports=file_unresolved.get(f.path, 0)),
            cycle_id=file_component.get(node_id),
        )

    # --- modules ------------------------------------------------------------------------------------------------------
    forced = {posixpath.dirname(f.path) for f in files if posixpath.basename(f.path) in ("package.json", "pyproject.toml", "setup.py") and posixpath.dirname(f.path)}
    forced |= {re.sub(r"(supabase/functions/[^/]+)/.*", r"\1", p) for p in code if re.search(r"supabase/functions/[^/]+/", p)}
    assignment = group_modules(sorted(code), forced)
    members: dict[str, list[str]] = defaultdict(list)
    for path, directory in assignment.items():
        members[directory].append(path)
    module_of = {f"file:{path}": _module_id(directory) for path, directory in assignment.items()}

    module_edge_files: dict[tuple[str, str], list[GraphEdge]] = defaultdict(list)
    for e in file_edges:
        a, b = module_of[e.source], module_of[e.target]
        if a != b:
            module_edge_files[(a, b)].append(e)
    module_edges: list[GraphEdge] = []
    for (a, b), group in module_edge_files.items():
        breakdown: dict[str, int] = defaultdict(int)
        for e in group:
            breakdown[e.relationship] += e.count
        samples = [e.evidence[0] for e in sorted(group, key=lambda x: (-x.count, x.id)) if e.evidence][:MAX_MODULE_EDGE_EVIDENCE]
        module_edges.append(GraphEdge(
            id=f"edge:{a}->{b}", source=a, target=b, level="module", relationship="depends_on", basis="aggregated", resolution="aggregated",
            confidence=VERIFIED if all(e.confidence == VERIFIED for e in group) else "mixed", count=len(group), breakdown=dict(sorted(breakdown.items())),
            evidence=samples, evidence_truncated=len(group) > len(samples),
        ))
    module_edges.sort(key=lambda e: e.id)
    module_eager: dict[str, set[str]] = defaultdict(set)
    for (a, b), group in module_edge_files.items():
        if any(e.relationship in EAGER for e in group):
            module_eager[a].add(b)
    module_cycles = strongly_connected(dict(module_eager))
    module_component = {n: f"cycle:module:{i}" for i, c in enumerate(module_cycles, 1) for n in c}
    for e in module_edges:
        if EAGER & set(e.breakdown) and e.source in module_component and module_component[e.source] == module_component.get(e.target):
            e.cycle_id = module_component[e.source]

    m_out: dict[str, set[str]] = defaultdict(set)
    m_in: dict[str, set[str]] = defaultdict(set)
    for e in module_edges:
        m_out[e.source].add(e.target)
        m_in[e.target].add(e.source)

    # --- areas ----------------------------------------------------------------------------------------------------------
    area_hits: dict[str, list[tuple[str, RoleHit]]] = defaultdict(list)
    for path, hits in roles.items():
        for hit in hits:
            if hit.role in ROLE_AREA:
                area_hits[ROLE_AREA[hit.role]].append((path, hit))
    if extra_database:
        area_hits.setdefault("database", [])
    rank = {"low": 0, "medium": 1, "high": 2}
    areas: list[Area] = []
    for kind in sorted(area_hits):
        hits = sorted(area_hits[kind], key=lambda ph: (-rank[ph[1].confidence], ph[0]))
        paths = sorted({p for p, _ in hits})
        evidence = [e for _, h in hits[:4] for e in h.evidence][:MAX_AREA_EVIDENCE]
        if kind == "database" and extra_database:
            evidence = (evidence + extra_database[: max(0, MAX_AREA_EVIDENCE - len(evidence))])[:MAX_AREA_EVIDENCE]
        by_module: dict[str, int] = defaultdict(int)
        for p in paths:
            by_module[module_of[f"file:{p}"]] += 1
        confidence = max((h.confidence for _, h in hits), key=rank.__getitem__) if hits else "medium"
        areas.append(Area(
            id=f"area:{kind}", kind=kind, label=AREA_LABEL[kind], confidence=confidence, file_count=len(paths),
            module_ids=[m for m, _ in sorted(by_module.items(), key=lambda kv: (-kv[1], kv[0]))], files=paths[:MAX_AREA_FILES], evidence=evidence,
        ))

    module_nodes: list[GraphNode] = []
    for directory, paths in sorted(members.items()):
        paths = sorted(paths)
        node_id = _module_id(directory)
        module_areas: dict[str, int] = defaultdict(int)
        sample: dict[str, list[Evidence]] = {}
        for p in paths:
            for h in roles.get(p, []):
                if h.role in ROLE_AREA:
                    module_areas[ROLE_AREA[h.role]] += 1
                    sample.setdefault(ROLE_AREA[h.role], list(h.evidence))
        dominant = max(module_areas.items(), key=lambda kv: (kv[1], kv[0]), default=None)
        has_entry = any(h.role == "edge_function" for p in paths for h in roles.get(p, []))  # a function directory is one deployable unit
        kind = "serverless" if has_entry else (dominant[0] if dominant and dominant[1] * 2 >= len(paths) else "directory")
        module_nodes.append(GraphNode(
            id=node_id, label=directory or "(root)", type="module", kind=kind, path=directory or ".", files=paths[:MAX_MODULE_FILES],
            file_count=len(paths), files_truncated=len(paths) > MAX_MODULE_FILES, area_ids=sorted(f"area:{a}" for a in module_areas),
            evidence=sample.get(kind, [])[:MAX_AREA_EVIDENCE] if kind != "directory" else [],
            metrics=NodeMetrics(
                incoming=len(m_in.get(node_id, ())), outgoing=len(m_out.get(node_id, ())),
                external_packages=len({n for p in paths for n in per_file_externals.get(p, {}) if not n.startswith("node:")}),
                unresolved_imports=sum(file_unresolved.get(p, 0) for p in paths),
            ),
            cycle_id=module_component.get(node_id),
        ))

    # --- technologies, externals, stats -----------------------------------------------------------------------------
    import_evidence: dict[tuple[str, str], list[Evidence]] = {
        key: list(by_file.values())[:3] for key, by_file in package_importers.items() if key[0] in ("python", "npm")
    }
    technologies = detect_technologies(deps, import_evidence)
    externals = sorted(
        (ExternalDependency(name=name, ecosystem=eco, importer_count=len(by_file), evidence=list(by_file.values())[:3])  # type: ignore[arg-type]
         for (eco, name), by_file in package_importers.items()),
        key=lambda d: (-d.importer_count, d.name),
    )

    def cycle_objects(level: str, components: list[list[str]], pool: list[GraphEdge]) -> list[Cycle]:
        by_cycle: dict[str, list[str]] = defaultdict(list)
        for e in pool:
            if e.cycle_id:
                by_cycle[e.cycle_id].append(e.id)
        return [
            Cycle(
                id=f"cycle:{level}:{i}", level=level, size=len(c), nodes=c[:MAX_CYCLE_LISTING],  # type: ignore[arg-type]
                edge_ids=by_cycle[f"cycle:{level}:{i}"][:MAX_CYCLE_LISTING * 2], truncated=len(c) > MAX_CYCLE_LISTING,
            )
            for i, c in enumerate(components, 1)
        ]

    all_cycles = cycle_objects("module", module_cycles, module_edges) + cycle_objects("file", file_cycles, file_edges)
    cycles = all_cycles[:MAX_CYCLES_LISTED]
    stats = Stats(
        files_total=len(files), code_files=len(selected), skipped_files=over_limit + missing_text + len(unparsed), languages=dict(sorted(languages.items())),
        modules=len(module_nodes), file_edges=len(file_edges), module_edges=len(module_edges), external_packages=len(package_importers),
        unresolved_imports=unresolved_total, non_code_imports=non_code, cycles=len(all_cycles), truncated=bool(over_limit),
    )
    if not selected:
        warnings.append("No supported source files (Python, JavaScript, TypeScript, TSX) were found in this snapshot")
    graph = Graph(
        commit_sha=commit_sha, analyzer_version=ANALYZER_VERSION, nodes=module_nodes, edges=module_edges, areas=areas, technologies=technologies,
        external_dependencies=externals[:MAX_EXTERNALS_IN_OVERVIEW], cycles=cycles, stats=stats, warnings=warnings,
    )
    detail = {
        "nodes": [n.model_dump(mode="json") for n in sorted(file_nodes.values(), key=lambda n: n.id)],
        "edges": [e.model_dump(mode="json") for e in file_edges],
        "module_of": module_of,
        "externals": [x.model_dump(mode="json") for x in sorted(external_by_file.values(), key=lambda x: (x.file, x.name))],
        "unresolved": [u.model_dump(mode="json") for u in unresolved],
    }
    return AnalysisResult(graph, detail)
