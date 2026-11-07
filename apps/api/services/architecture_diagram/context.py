"""Deterministic, bounded context selection for Architecture Diagram V2.

Reuses what already exists instead of re-deriving it: the deterministic import graph
(`architecture_analyses`, built by `services/architecture/build.py`) and the stored file snapshot
(`source_files`, `code_chunks`). Nothing here parses source, extracts imports, or touches an AI provider.

Two layers:

* `select_context` is a pure function over already-fetched, plain data. Given the same inputs it returns
  byte-identical output, and it enforces a hard character budget by dropping the lowest-priority items
  first (representative excerpts, then manifests, then entry points) rather than ever growing unbounded.
* `load_architecture_context` is the thin async database glue that fetches those inputs and calls it.
"""

from __future__ import annotations

import json
import posixpath
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import Settings, get_settings
from apps.api.models.entities import ArchitectureAnalysis, CodeChunk, Repository, SourceFile
from apps.api.schemas.architecture import FileExternal, Graph, GraphEdge, GraphNode
from apps.api.services.architecture import store as architecture_store
from apps.api.services.architecture_diagram.excerpt import call_site_excerpt
from apps.api.services.architecture_diagram.infrastructure import InfraItem, detect_infrastructure

# File-node kinds worth surfacing as entry points: roughly "where execution or a request starts".
ENTRY_KINDS = ("entrypoint", "route", "page", "edge_function", "job", "model")
# Representative-chunk kinds preferred over a plain "code"/"imports"/"text" slice: they carry a named
# definition, which is far denser signal per character than an arbitrary body slice.
NAMED_CHUNK_KINDS = frozenset({"class", "function", "method", "interface", "enum", "namespace"})
ROOT_MANIFEST_BASENAMES = (
    "package.json", "pyproject.toml", "setup.py", "cargo.toml", "go.mod", "composer.json",
    "requirements.txt", "gemfile",
)

# How strongly a file's deterministically-detected `kind` (role) suggests it is a primary feature
# implementation worth showing the model, versus plain, unclassified source. Lower tier wins. This is NOT
# an import-degree ranking: a file's architectural significance is about what it does, which raw fan-in
# cannot tell. A heavily-imported `utils.ts` has high fan-in and near-zero significance; a feature file
# like `purchases.ts` or `captureSession.ts` (GitDiagram's ClassLens example) is often imported by nobody
# but IS the architecture. There is no "service" or "controller" role in `services/architecture/signals.py`
# (it only recognizes framework-specific constructs), so most real feature-implementation files land in
# the generic "source" tier below and are ranked there by `_generic_penalty`/line count instead.
PRIMARY_ROLE_TIER: dict[str, int] = {
    "entrypoint": 0, "route": 0, "edge_function": 0, "page": 0, "job": 0, "model": 0,
    "migration": 1, "component": 1,
    "source": 2,
    "test": 3,
}
_SOURCE_TIER = PRIMARY_ROLE_TIER["source"]

# Generic/boilerplate basenames: a naming-convention signal, not a frontend/backend template, so it applies
# the same way to a Python package, a JS app, or a Go module. Deprioritized within a file's tier, never
# excluded outright — a module may have nothing else to show.
GENERIC_BASENAME_STEMS = frozenset({
    "utils", "util", "helpers", "helper", "common", "lib", "misc", "shared", "constants",
    "config", "settings", "types", "index", "__init__", "main", "base",
})


def _generic_penalty(path: str) -> int:
    stem = posixpath.splitext(posixpath.basename(path))[0].lower()
    return 1 if stem in GENERIC_BASENAME_STEMS else 0


def _significance_key(node: GraphNode, line_count: int) -> tuple[int, int, int, str]:
    """Sort key for representative-file selection; smaller is more significant.

    In order: (1) role tier — an actual entry point, route, job, model etc. beats plain source; (2) a
    generic/boilerplate filename is pushed behind a domain-specific one within the same tier; (3) line
    count (already stored at ingestion, no re-parsing) as a cheap, free proxy for "how much is actually
    implemented here" — deliberately not import degree; (4) path, for determinism.
    """
    tier = PRIMARY_ROLE_TIER.get(node.kind, _SOURCE_TIER)
    return (tier, _generic_penalty(node.path), -line_count, node.path)


def _subdir_within_module(module_path: str, file_path: str) -> str:
    """The file's immediate subdirectory relative to its module's own path ("" if it sits directly in the
    module). Used to give a large module's distinct sub-areas (controllers/, models/, routes/) their own
    excerpt slot instead of letting two files from the same corner of a 30-file module win every slot."""
    prefix = "" if module_path in ("", ".") else module_path + "/"
    rest = file_path[len(prefix):] if file_path.startswith(prefix) else file_path
    return rest.split("/", 1)[0] if "/" in rest else ""


class ArchitectureDiagramContextError(Exception):
    """Base class for errors raised while loading or selecting diagram context."""


class RepositoryNotFoundError(ArchitectureDiagramContextError):
    pass


class NoArchitectureAnalysisError(ArchitectureDiagramContextError):
    """No completed deterministic analysis exists for this repository."""


class StaleArchitectureAnalysisError(ArchitectureDiagramContextError):
    """The stored deterministic analysis does not describe the repository's current ingested commit.

    Diagram generation refuses to run against a stale graph rather than silently mixing an old module/edge
    picture with a newer one; the caller should (re)request `/architecture` first.
    """


# --- selected context (what the prompt renderer sees) -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContextModuleFile:
    """A real path the model may ground a component in, even where no code excerpt was pulled for it.
    This is what makes a large module's files other than the 1-2 excerpted ones visible at all — without
    it, a 30-file module with two shown files leaves the other 28 structurally invisible to the model, no
    matter how generous the excerpt budget is."""

    path: str
    kind: str  # the file's detected role ("source" if none matched)


@dataclass(frozen=True, slots=True)
class ContextModule:
    id: str
    label: str
    path: str
    kind: str
    file_count: int
    roles: tuple[str, ...]
    area_ids: tuple[str, ...]
    files: tuple[ContextModuleFile, ...] = ()  # significance-ranked, capped; may be fewer than file_count


@dataclass(frozen=True, slots=True)
class ContextEdge:
    source: str
    target: str
    count: int
    breakdown: dict[str, int]


@dataclass(frozen=True, slots=True)
class ContextFileReference:
    """One already-shown file and the other already-shown files it imports, from the real (Tree-sitter)
    file-level import graph — not inferred. Two files sharing a module have no module-level edge to show
    this (a module never depends on itself), which is exactly the case that most needs it: it is real,
    citable evidence for a relationship between two components whose code happens to live in the same
    directory, and `validate.py` uses it to verify such a relationship instead of leaving it "inferred"
    only because the deterministic graph's own module granularity couldn't see it."""

    source: str
    targets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContextCandidateComponent:
    """One file from a directory with several same-role siblings (controllers/, pages/, routes/ — exactly
    the shape that collapses into one generic "Controllers"/"UI Pages" bucket otherwise), called out
    explicitly so the model treats it as its own feature candidate rather than merging it with its siblings
    merely because they share a directory or technical role. `imports`/`imported_by` (restricted to other
    candidates) is the one deterministic signal for when two candidates are *not* independent: a direct
    file-level edge between two siblings is real coupling; its absence is not proof of independence, but
    its presence is real evidence they may belong in one component."""

    path: str
    module_id: str
    kind: str
    imports: tuple[str, ...]
    imported_by: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContextArea:
    kind: str
    label: str
    confidence: str
    file_count: int


@dataclass(frozen=True, slots=True)
class ContextTechnology:
    name: str
    category: str


@dataclass(frozen=True, slots=True)
class ContextExternalDependency:
    name: str
    ecosystem: str
    importer_count: int


@dataclass(frozen=True, slots=True)
class ContextEntryPoint:
    path: str
    kind: str
    module_id: str


@dataclass(frozen=True, slots=True)
class ContextDoc:
    path: str
    content: str


@dataclass(frozen=True, slots=True)
class ExcerptCandidate:
    """One named-symbol slice of a representative file, as already stored in `code_chunks`."""

    path: str
    module_id: str
    symbol: str | None
    kind: str
    content: str


@dataclass(frozen=True, slots=True)
class ContextExcerpt:
    path: str
    module_id: str
    symbol: str | None
    content: str


@dataclass(frozen=True, slots=True)
class ArchitectureContext:
    repo_full_name: str
    commit_sha: str
    modules: tuple[ContextModule, ...]
    module_edges: tuple[ContextEdge, ...]
    areas: tuple[ContextArea, ...]
    technologies: tuple[ContextTechnology, ...]
    external_dependencies: tuple[ContextExternalDependency, ...]
    cycles_count: int
    entry_points: tuple[ContextEntryPoint, ...]
    readme_excerpt: str | None
    manifests: tuple[ContextDoc, ...]
    excerpts: tuple[ContextExcerpt, ...]
    file_references: tuple[ContextFileReference, ...]
    candidate_components: tuple[ContextCandidateComponent, ...]
    stats: dict[str, object]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    infrastructure: tuple[InfraItem, ...] = ()

    def to_json_dict(self) -> dict[str, object]:
        """A deterministic (sorted-key-stable) plain-dict view, for prompt rendering and tests."""
        return {
            "repository": self.repo_full_name,
            "commit": self.commit_sha[:12],
            "modules": [asdict(m) for m in self.modules],
            "module_dependencies": [asdict(e) for e in self.module_edges],
            "areas": [asdict(a) for a in self.areas],
            "technologies": [asdict(t) for t in self.technologies],
            "external_dependencies": [asdict(d) for d in self.external_dependencies],
            "cycles_count": self.cycles_count,
            "entry_points": [asdict(p) for p in self.entry_points],
            "readme_excerpt": self.readme_excerpt,
            "manifests": [{"path": d.path, "excerpt": d.content} for d in self.manifests],
            "representative_excerpts": [
                {"path": e.path, "module_id": e.module_id, "symbol": e.symbol, "content": e.content} for e in self.excerpts
            ],
            "file_references": {r.source: list(r.targets) for r in self.file_references},
            "candidate_components": [asdict(c) for c in self.candidate_components],
            "infrastructure": [
                {"category": i.category, "name": i.name, "strength": i.strength,
                 "evidence": [{"path": e.path, "line": e.line, "text": e.text} for e in i.evidence]}
                for i in self.infrastructure
            ],
            "stats": self.stats,
        }


# --- pure selection ------------------------------------------------------------------------------------------


def select_context(
    *,
    graph: Graph,
    repo_full_name: str,
    file_nodes: Sequence[GraphNode],
    module_of: Mapping[str, str],
    line_counts: Mapping[str, int],
    docs: Sequence[ContextDoc],
    manifests: Sequence[ContextDoc],
    excerpt_candidates: Sequence[ExcerptCandidate],
    file_edges: Sequence[GraphEdge] = (),
    source_texts: Mapping[str, str] | None = None,
    file_externals: Sequence[FileExternal] = (),
    infrastructure_texts: Mapping[str, str] | None = None,
    settings: Settings | None = None,
) -> ArchitectureContext:
    """Build bounded, deterministic context from already-fetched, plain inputs.

    `docs` is README-like files (already filtered to root-level docs by the caller); `manifests` is
    package/dependency manifests; `excerpt_candidates` is named-symbol chunks of representative files
    (see `select_representative_files`); `line_counts` feeds the same significance ranking used there, for
    each module's own `files` listing. Every list is capped; items dropped for a cap are recorded in
    `warnings`, never silently discarded without a trace. The overall rendered size is then checked against
    `architecture_diagram_context_max_chars` and trimmed further (excerpts, then manifests, then entry
    points, in that order) if still too large.
    """
    settings = settings or get_settings()
    warnings: list[str] = []

    modules = sorted(
        (n for n in graph.nodes if n.type == "module"), key=lambda n: (-n.file_count, n.id)
    )
    if len(modules) > settings.architecture_diagram_max_modules:
        warnings.append(f"{len(modules) - settings.architecture_diagram_max_modules} smaller module(s) omitted from context")
    modules = modules[: settings.architecture_diagram_max_modules]
    wanted_module_ids = {m.id for m in modules}

    # Every file in every kept module, ranked the same way `select_representative_files` ranks excerpt
    # candidates: role tier, then a generic-filename penalty, then line count. This is what lets the model
    # ground a component in a file it was never given a code excerpt for — the fix for a large module's
    # files beyond the 1-2 excerpted ones being otherwise invisible.
    files_by_module: dict[str, list[GraphNode]] = {}
    for f in file_nodes:
        if f.type != "file":
            continue
        module_id = module_of.get(f.path)
        if module_id in wanted_module_ids:
            files_by_module.setdefault(module_id, []).append(f)

    context_modules = tuple(
        ContextModule(
            id=m.id, label=m.label, path=m.path, kind=m.kind, file_count=m.file_count,
            roles=tuple(m.roles), area_ids=tuple(m.area_ids),
            files=tuple(
                ContextModuleFile(path=f.path, kind=f.kind)
                for f in sorted(
                    files_by_module.get(m.id, ()), key=lambda f: _significance_key(f, line_counts.get(f.path, 0))
                )[: settings.architecture_diagram_max_files_per_module]
            ),
        )
        for m in modules
    )
    kept_module_ids = {m.id for m in context_modules}

    # File-level references among files already shown via a module's `files` listing — the only way the
    # model can be shown a real, citable connection between two files that happen to share one module (a
    # module has no edge to itself). Restricted to shown files on both ends: a reference to a file the
    # model was never told exists would be unverifiable noise, not evidence.
    shown_paths = {f.path for m in context_modules for f in m.files}
    references_by_source: dict[str, set[str]] = {}
    for e in file_edges:
        if e.level != "file":
            continue
        source_path, target_path = e.source[5:], e.target[5:]
        if source_path in shown_paths and target_path in shown_paths and source_path != target_path:
            references_by_source.setdefault(source_path, set()).add(target_path)
    reference_sources = sorted(references_by_source)
    if len(reference_sources) > settings.architecture_diagram_max_file_reference_sources:
        warnings.append(
            f"{len(reference_sources) - settings.architecture_diagram_max_file_reference_sources} file reference "
            "source(s) omitted from context"
        )
    reference_sources = reference_sources[: settings.architecture_diagram_max_file_reference_sources]
    context_file_references = tuple(
        ContextFileReference(
            source=path,
            targets=tuple(sorted(references_by_source[path])[: settings.architecture_diagram_max_targets_per_file_reference]),
        )
        for path in reference_sources
    )

    # Candidate components: files from a (module, immediate subdirectory) bucket with several same-tier
    # siblings — the exact shape that collapses into one generic "Controllers"/"UI Pages" bucket otherwise,
    # because nothing else in the context distinguishes "six parallel feature files" from "six incidental
    # utility files". Tests are excluded: a test file names what it tests, not a feature implementation.
    # A bucket below the minimum is a module too small to need this — already fully visible via `files`.
    kind_by_path = {f.path: f.kind for f in file_nodes if f.type == "file"}
    candidate_buckets: dict[tuple[str, str], list[GraphNode]] = {}
    for m in context_modules:
        for f in files_by_module.get(m.id, ()):
            if PRIMARY_ROLE_TIER.get(f.kind, _SOURCE_TIER) >= PRIMARY_ROLE_TIER["test"]:
                continue
            candidate_buckets.setdefault((m.id, _subdir_within_module(m.path, f.path)), []).append(f)

    candidate_paths: list[str] = []
    for key in sorted(candidate_buckets, key=lambda k: (-len(candidate_buckets[k]), k)):
        bucket = candidate_buckets[key]
        if len(bucket) < settings.architecture_diagram_candidate_min_siblings:
            continue
        ranked = sorted(bucket, key=lambda f: _significance_key(f, line_counts.get(f.path, 0)))
        candidate_paths.extend(f.path for f in ranked[: settings.architecture_diagram_max_candidates_per_bucket])
    if len(candidate_paths) > settings.architecture_diagram_max_candidate_components:
        warnings.append(f"{len(candidate_paths) - settings.architecture_diagram_max_candidate_components} candidate component(s) omitted from context")
    candidate_paths = candidate_paths[: settings.architecture_diagram_max_candidate_components]
    candidate_set = set(candidate_paths)
    context_candidates = tuple(
        ContextCandidateComponent(
            path=path, module_id=module_of.get(path, "module:."), kind=kind_by_path.get(path, "source"),
            imports=tuple(sorted(t for t in references_by_source.get(path, ()) if t in candidate_set)),
            imported_by=tuple(sorted(s for s in candidate_set if path in references_by_source.get(s, ()))),
        )
        for path in sorted(candidate_paths)
    )

    edges = sorted(
        (e for e in graph.edges if e.level == "module" and e.source in kept_module_ids and e.target in kept_module_ids),
        key=lambda e: (-e.count, e.id),
    )
    if len(edges) > settings.architecture_diagram_max_module_edges:
        warnings.append(f"{len(edges) - settings.architecture_diagram_max_module_edges} smaller module dependency edge(s) omitted from context")
    edges = edges[: settings.architecture_diagram_max_module_edges]
    context_edges = tuple(ContextEdge(source=e.source, target=e.target, count=e.count, breakdown=dict(e.breakdown)) for e in edges)

    areas = tuple(
        ContextArea(kind=a.kind, label=a.label, confidence=a.confidence, file_count=a.file_count)
        for a in sorted(graph.areas, key=lambda a: a.kind)
    )
    technologies = tuple(
        ContextTechnology(name=t.name, category=t.category)
        for t in sorted(graph.technologies, key=lambda t: t.name)[: settings.architecture_diagram_max_technologies]
    )
    externals = tuple(
        ContextExternalDependency(name=d.name, ecosystem=d.ecosystem, importer_count=d.importer_count)
        for d in graph.external_dependencies[: settings.architecture_diagram_max_external_dependencies]
    )

    entry_rank = {kind: i for i, kind in enumerate(ENTRY_KINDS)}
    entries = sorted(
        (f for f in file_nodes if f.type == "file" and f.kind in entry_rank),
        key=lambda f: (entry_rank[f.kind], f.path),
    )
    if len(entries) > settings.architecture_diagram_max_entry_points:
        warnings.append(f"{len(entries) - settings.architecture_diagram_max_entry_points} entry point(s) omitted from context")
    entries = entries[: settings.architecture_diagram_max_entry_points]
    context_entries = [
        ContextEntryPoint(path=f.path, kind=f.kind, module_id=module_of[f.path])
        for f in entries
        if module_of.get(f.path) in kept_module_ids
    ]
    context_entries_t = tuple(context_entries)

    readme = next(iter(docs), None)
    readme_excerpt = _clip_text(readme.content, settings.architecture_diagram_readme_max_chars) if readme else None

    manifest_list = list(manifests[: settings.architecture_diagram_max_manifests])
    if len(manifests) > settings.architecture_diagram_max_manifests:
        warnings.append(f"{len(manifests) - settings.architecture_diagram_max_manifests} manifest(s) omitted from context")
    context_manifests = tuple(
        ContextDoc(path=m.path, content=_clip_text(m.content, settings.architecture_diagram_manifest_max_chars))
        for m in manifest_list
    )

    by_path: dict[str, list[ExcerptCandidate]] = {}
    order: list[str] = []
    for c in excerpt_candidates:
        if c.path not in by_path:
            by_path[c.path] = []
            order.append(c.path)
        by_path[c.path].append(c)
    if len(order) > settings.architecture_diagram_max_excerpt_files:
        warnings.append(f"{len(order) - settings.architecture_diagram_max_excerpt_files} representative file excerpt(s) omitted from context")
    order = order[: settings.architecture_diagram_max_excerpt_files]

    # Call-site excerpts: for a file whose stored source is available, show real numbered lines around its
    # uses of imported names (import lines come from the file-edge evidence), instead of the head chunks.
    import_ranges: dict[str, list[tuple[int, int]]] = {}
    for e in file_edges:
        if e.level == "file":
            import_ranges.setdefault(e.source[5:], []).extend((ev.start_line, ev.end_line) for ev in e.evidence)

    context_excerpts: list[ContextExcerpt] = []
    for path in order:
        if source_texts and path in source_texts:
            text = call_site_excerpt(
                source_texts[path], settings.architecture_diagram_excerpt_max_chars_per_file,
                import_line_ranges=sorted(set(import_ranges.get(path, ()))),
            )
            if text.strip():
                context_excerpts.append(ContextExcerpt(path=path, module_id=module_of.get(path, "module:."), symbol=None, content=text))
                continue
        candidates = sorted(by_path[path], key=lambda c: (c.kind not in NAMED_CHUNK_KINDS, c.symbol or ""))
        budget = settings.architecture_diagram_excerpt_max_chars_per_file
        pieces: list[str] = []
        used = 0
        for cand in candidates:
            if used >= budget:
                break
            piece = cand.content if used + len(cand.content) <= budget else cand.content[: budget - used]
            pieces.append(piece)
            used += len(piece)
        if pieces:
            context_excerpts.append(ContextExcerpt(path=path, module_id=candidates[0].module_id, symbol=candidates[0].symbol, content="\n…\n".join(pieces)))

    context = ArchitectureContext(
        repo_full_name=repo_full_name,
        commit_sha=graph.commit_sha,
        modules=context_modules,
        module_edges=context_edges,
        areas=areas,
        technologies=technologies,
        external_dependencies=externals,
        cycles_count=len(graph.cycles),
        entry_points=context_entries_t,
        readme_excerpt=readme_excerpt,
        manifests=context_manifests,
        excerpts=tuple(context_excerpts),
        file_references=context_file_references,
        candidate_components=context_candidates,
        infrastructure=detect_infrastructure(graph.technologies, graph.external_dependencies, file_externals, infrastructure_texts or {}),
        stats={
            "files_total": graph.stats.files_total, "code_files": graph.stats.code_files,
            "modules_total": len(graph.nodes), "modules_shown": len(context_modules),
        },
        warnings=tuple(warnings),
    )
    return _enforce_budget(context, settings)


def _enforce_budget(context: ArchitectureContext, settings: Settings) -> ArchitectureContext:
    """Drop the lowest-priority sections, one item at a time, until the rendered size fits the budget.

    Priority (first to go): file references, then candidate components, then representative excerpts, then
    manifests, then entry points. File references and candidate components go first because they are the
    newest, most experimental additions — dropping them first means every prior size/trimming test keeps
    its exact old behavior unless a repository is large enough for them to matter at all. Structural facts
    (modules, module dependencies, areas, technologies, externals) are never dropped here — they are
    already capped by count above and are the part of the context worth paying for.
    """
    budget = settings.architecture_diagram_context_max_chars
    references = list(context.file_references)
    candidates = list(context.candidate_components)
    excerpts, manifests, entries = list(context.excerpts), list(context.manifests), list(context.entry_points)
    warnings = list(context.warnings)

    def size() -> int:
        return len(json.dumps(_rendered(context, references, candidates, excerpts, manifests, entries), sort_keys=True, default=str))

    while size() > budget and (references or candidates or excerpts or manifests or entries):
        if references:
            references.pop()
            warnings.append("a file reference entry was dropped to stay within the context size budget")
        elif candidates:
            candidates.pop()
            warnings.append("a candidate component was dropped to stay within the context size budget")
        elif excerpts:
            excerpts.pop()
            warnings.append("a representative excerpt was dropped to stay within the context size budget")
        elif manifests:
            manifests.pop()
            warnings.append("a manifest excerpt was dropped to stay within the context size budget")
        else:
            entries.pop()
            warnings.append("an entry point was dropped to stay within the context size budget")
    if (
        references == list(context.file_references)
        and candidates == list(context.candidate_components)
        and excerpts == list(context.excerpts)
        and manifests == list(context.manifests)
        and entries == list(context.entry_points)
    ):
        return context
    return ArchitectureContext(
        repo_full_name=context.repo_full_name, commit_sha=context.commit_sha, modules=context.modules,
        module_edges=context.module_edges, areas=context.areas, technologies=context.technologies,
        external_dependencies=context.external_dependencies, cycles_count=context.cycles_count,
        entry_points=tuple(entries), readme_excerpt=context.readme_excerpt, manifests=tuple(manifests),
        excerpts=tuple(excerpts), file_references=tuple(references), candidate_components=tuple(candidates),
        stats=context.stats, warnings=tuple(warnings),
        infrastructure=context.infrastructure,
    )


def _clip_text(text: str, max_chars: int) -> str:
    text = text.strip()
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"


def _rendered(
    context: ArchitectureContext, references: list[ContextFileReference], candidates: list[ContextCandidateComponent],
    excerpts: list[ContextExcerpt], manifests: list[ContextDoc], entries: list[ContextEntryPoint],
) -> dict[str, object]:
    """`context.to_json_dict()` with candidate (still being trimmed) section lists substituted in, so
    `_enforce_budget` can measure a prospective size before committing to it."""
    rendered = context.to_json_dict()
    rendered["entry_points"] = [asdict(p) for p in entries]
    rendered["manifests"] = [{"path": d.path, "excerpt": d.content} for d in manifests]
    rendered["representative_excerpts"] = [{"path": e.path, "module_id": e.module_id, "symbol": e.symbol, "content": e.content} for e in excerpts]
    rendered["file_references"] = {r.source: list(r.targets) for r in references}
    rendered["candidate_components"] = [asdict(c) for c in candidates]
    return rendered


# --- database glue ------------------------------------------------------------------------------------------


@dataclass(slots=True)
class LoadedArchitecture:
    """Everything `generate.py` needs: the selected context, plus the deterministic graph and snapshot facts
    `validate.py` cross-checks the model's output against."""

    context: ArchitectureContext
    graph: Graph
    known_paths: frozenset[str]
    module_of: Mapping[str, str]  # plain file path -> module id ("module:<dir>"), not "file:"-prefixed
    file_edges: tuple[GraphEdge, ...]  # real file-to-file import edges, from the stored analysis's `detail`
    repo_full_name: str
    commit_sha: str


async def load_architecture_context(
    db: AsyncSession, repository_id: int, *, settings: Settings | None = None
) -> LoadedArchitecture:
    """Load everything needed for diagram generation from the existing deterministic analysis and the
    stored file snapshot. Raises `RepositoryNotFoundError`, `NoArchitectureAnalysisError` or
    `StaleArchitectureAnalysisError`; never re-parses or re-analyzes anything itself.
    """
    settings = settings or get_settings()
    try:
        status, graph = await architecture_store.get_overview(db, repository_id, settings)
    except architecture_store.RepositoryNotFoundError as exc:
        raise RepositoryNotFoundError(str(exc)) from exc
    except architecture_store.NoResultError as exc:
        raise NoArchitectureAnalysisError(str(exc)) from exc
    if status.freshness != "current":
        raise StaleArchitectureAnalysisError(
            f"The stored architecture analysis is {status.freshness}; refresh it (POST /architecture) before generating a diagram"
        )
    commit_sha = graph.commit_sha

    repository = await db.get(Repository, repository_id)
    if repository is None:  # pragma: no cover - get_overview would already have raised
        raise RepositoryNotFoundError(f"Repository {repository_id} not found")

    detail_row = (
        await db.execute(select(ArchitectureAnalysis.detail).where(ArchitectureAnalysis.repository_id == repository_id))
    ).scalar_one_or_none()
    detail = detail_row or {}
    file_nodes = [GraphNode.model_validate(n) for n in detail.get("nodes", [])]
    file_externals = [FileExternal.model_validate(x) for x in detail.get("externals", [])]
    file_edges = tuple(GraphEdge.model_validate(e) for e in detail.get("edges", []))
    module_of_raw: dict[str, str] = detail.get("module_of", {})
    module_of = {path[5:] if path.startswith("file:") else path: module_id for path, module_id in module_of_raw.items()}

    scope = (SourceFile.repository_id == repository_id, SourceFile.commit_sha == commit_sha)
    path_rows = (await db.execute(select(SourceFile.path, SourceFile.line_count).where(*scope))).all()
    known_paths = frozenset(path for path, _ in path_rows)
    line_counts = {path: line_count for path, line_count in path_rows}

    doc_rows = (
        await db.execute(select(SourceFile.path, SourceFile.content).where(*scope, SourceFile.category == "docs"))
    ).all()
    readme_candidates = sorted(
        (ContextDoc(path, content) for path, content in doc_rows if posixpath.basename(path).lower().startswith("readme") and "/" not in path),
        key=lambda d: d.path,
    )

    manifest_rows = (
        await db.execute(select(SourceFile.path, SourceFile.content).where(*scope, SourceFile.category == "config"))
    ).all()
    manifests = sorted(
        (ContextDoc(path, content) for path, content in manifest_rows if posixpath.basename(path).lower() in ROOT_MANIFEST_BASENAMES),
        key=lambda d: (d.path.count("/"), d.path),
    )

    representative_paths = select_representative_files(graph, file_nodes, module_of, line_counts, settings)
    excerpt_candidates: list[ExcerptCandidate] = []
    source_texts: dict[str, str] = {}
    if representative_paths:
        source_texts = {
            path: content
            for path, content in (
                await db.execute(select(SourceFile.path, SourceFile.content).where(*scope, SourceFile.path.in_(representative_paths)))
            ).all()
            if content
        }
        chunk_rows = (
            await db.execute(
                select(SourceFile.path, CodeChunk.kind, CodeChunk.symbol_name, CodeChunk.content)
                .join(SourceFile, SourceFile.id == CodeChunk.source_file_id)
                .where(*scope, SourceFile.path.in_(representative_paths))
                .order_by(SourceFile.path, CodeChunk.chunk_index)
            )
        ).all()
        for path, kind, symbol_name, content in chunk_rows:
            excerpt_candidates.append(ExcerptCandidate(path=path, module_id=module_of.get(path, "module:."), symbol=symbol_name, kind=kind, content=content))

    # Stored source of files that import a package, are entry points or candidates: scanned for SDK usage lines.
    scan_paths = sorted(
        {x.file for x in file_externals}
        | {f.path for f in file_nodes if f.type == "file" and f.kind in ENTRY_KINDS}
    )[: settings.architecture_diagram_max_infrastructure_scan_files]
    infrastructure_texts: dict[str, str] = {}
    if scan_paths:
        infrastructure_texts = {
            path: content
            for path, content in (
                await db.execute(select(SourceFile.path, SourceFile.content).where(*scope, SourceFile.path.in_(scan_paths)))
            ).all()
            if content
        }

    context = select_context(
        graph=graph, repo_full_name=repository.full_name, file_nodes=file_nodes, module_of=module_of,
        line_counts=line_counts, docs=readme_candidates, manifests=manifests,
        excerpt_candidates=excerpt_candidates, file_edges=file_edges, source_texts=source_texts, file_externals=file_externals, infrastructure_texts=infrastructure_texts, settings=settings,
    )
    return LoadedArchitecture(
        context=context, graph=graph, known_paths=known_paths, module_of=module_of, file_edges=file_edges,
        repo_full_name=repository.full_name, commit_sha=commit_sha,
    )


def select_representative_files(
    graph: Graph,
    file_nodes: Sequence[GraphNode],
    module_of: Mapping[str, str],
    line_counts: Mapping[str, int],
    settings: Settings,
) -> list[str]:
    """Pick which files' code the model actually gets to see: breadth first, then depth, within units fine
    enough that a large module's distinct sub-areas each get a turn.

    The unit is not the module but (module, immediate subdirectory within it): a 30-file module like
    `backend/` with `controllers/`, `models/`, `routes/` and `middlewares/` subdirectories gets one
    candidate file from *each* before any of them gets a second — otherwise two files from whichever
    subdirectory ranks highest would fill the module's entire excerpt budget, and the model would never
    see that the others exist (this was the main cause of under-detailed diagrams: large modules collapsed
    to 1-2 files total). A module with no further subdirectory structure degenerates to one bucket, so this
    is a strict refinement of the old "one bucket per module" behavior, not a different shape for simple
    repos.

    Within a bucket, files are ranked by `_significance_key` (role tier, then a generic-filename penalty,
    then line count) — never by how many other files import them. GitDiagram-style feature files
    (ClassLens's `purchases.ts`, `captureSession.ts`) are typically imported by almost nobody but implement
    the actual behavior; a heavily-imported `utils.ts` ranks behind them here on purpose.
    """
    module_by_id = {n.id: n for n in graph.nodes if n.type == "module"}
    buckets: dict[tuple[str, str], list[GraphNode]] = {}
    for f in file_nodes:
        if f.type != "file":
            continue
        module = module_by_id.get(module_of.get(f.path, ""))
        if module is None:
            continue
        buckets.setdefault((module.id, _subdir_within_module(module.path, f.path)), []).append(f)

    module_order = sorted(module_by_id.values(), key=lambda n: (-n.file_count, n.id))
    module_rank = {m.id: i for i, m in enumerate(module_order)}
    bucket_order = sorted(buckets, key=lambda key: (module_rank.get(key[0], len(module_order)), key[1]))
    ranked_by_bucket = {
        key: sorted(files, key=lambda f: _significance_key(f, line_counts.get(f.path, 0))) for key, files in buckets.items()
    }

    picked: list[str] = []
    for round_index in range(max(settings.architecture_diagram_max_excerpts_per_bucket, 1)):
        for key in bucket_order:
            if len(picked) >= settings.architecture_diagram_max_excerpt_files:
                return picked
            ranked = ranked_by_bucket[key]
            if round_index < len(ranked):
                picked.append(ranked[round_index].path)
    return picked
