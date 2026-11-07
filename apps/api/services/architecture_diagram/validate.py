"""Parses the model's JSON response and seals it into a validated `ArchitectureDiagram`.

The model's `RawArchitectureDiagram` is a hypothesis, never ground truth. Every `source_paths` entry is
checked against the repository's real file snapshot; every relationship's confidence and evidence is
computed from scratch against the deterministic import graph (`schemas/architecture.py`) and nothing the
model wrote is ever copied into `confidence` or `evidence` — the same discipline `services/rag.py`'s
`finalize_answer` uses for citations: trust the retrieval/analysis layer, not the model's claims about it.

Critically: `confidence="verified"` is only ever reachable through `kind="imports"` (see
`schemas.architecture_diagram.IMPORT_VERIFIABLE_KINDS`). A relationship proposed as `sync_call`,
`async_call`, `queue`, `db_read` or `db_write` is *always* `"inferred"`, however strong the import edge
between the same two modules happens to be — a static import proves a compile-time dependency, not that an
HTTP call, a queue hop or a database read/write actually happens at runtime.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Mapping, Sequence
from itertools import product
from typing import TYPE_CHECKING

from pydantic import ValidationError

from apps.api.config import Settings, get_settings
from apps.api.schemas.architecture import Graph, GraphEdge

if TYPE_CHECKING:
    from apps.api.services.architecture_diagram.context import ContextCandidateComponent
    from apps.api.services.architecture_diagram.infrastructure import InfraItem

from apps.api.services.architecture_diagram.infrastructure import is_represented
from apps.api.schemas.architecture_diagram import (
    DIAGRAM_GENERATOR_VERSION,
    IMPORT_VERIFIABLE_KINDS,
    ArchitectureDiagram,
    Component,
    Confidence,
    DiagramEvidence,
    EvidenceBasis,
    Group,
    RawArchitectureDiagram,
    RawComponent,
    Relationship,
)


class DiagramGenerationError(Exception):
    """The model's response could not be turned into a diagram.

    `code`: `invalid_json` (no parseable JSON object in the response) or `invalid_shape` (parsed, but does
    not match `RawArchitectureDiagram`). Distinct from `services.generation.GenerationError`, which is a
    provider/transport failure; this is a failure of the provider's otherwise-successful output.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


_FENCE = re.compile(r"^```(?:json)?[ \t]*\n?|\n?```[ \t]*$")


def parse_raw_diagram(text: str) -> RawArchitectureDiagram:
    """Extract the model's JSON object (tolerating a markdown code fence around it) and parse it strictly.

    Raises `DiagramGenerationError`; never falls back to guessing a shape for malformed output.
    """
    cleaned = _FENCE.sub("", text.strip()).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise DiagramGenerationError("invalid_json", "The model's response contained no JSON object")
    try:
        data = json.loads(cleaned[start : end + 1])
    except ValueError as exc:
        raise DiagramGenerationError("invalid_json", f"The model's response was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DiagramGenerationError("invalid_shape", "The model's response was not a JSON object")
    try:
        return RawArchitectureDiagram.model_validate(data)
    except ValidationError as exc:
        raise DiagramGenerationError("invalid_shape", f"The model's response did not match the expected shape: {exc}") from exc


def _looks_like_bare_rename(component: RawComponent, valid_paths: Sequence[str]) -> bool:
    """A heuristic, non-fatal signal that a component may be a folder renamed rather than a functional
    description: a near-empty description, with a label that is just one of its own path segments."""
    if len(component.description.strip()) >= 20:
        return False
    label = re.sub(r"[\s_-]+", "", component.label.lower())
    if not label:
        return False
    for path in valid_paths:
        trimmed = path.rstrip("/")
        base = posixpath.splitext(posixpath.basename(trimmed))[0]
        parent = posixpath.basename(posixpath.dirname(trimmed))
        if label in (re.sub(r"[\s_-]+", "", base.lower()), re.sub(r"[\s_-]+", "", parent.lower())):
            return True
    return False


def validate_diagram(
    raw: RawArchitectureDiagram,
    *,
    graph: Graph,
    known_paths: Sequence[str] | frozenset[str],
    module_of: Mapping[str, str],
    commit_sha: str,
    file_edges: Sequence[GraphEdge] = (),
    candidate_components: Sequence["ContextCandidateComponent"] = (),
    infrastructure: Sequence["InfraItem"] = (),
    settings: Settings | None = None,
) -> ArchitectureDiagram:
    """Seal `raw` into an `ArchitectureDiagram`: filter paths to ones that really exist, cap every list to
    a bounded size, and (re)compute every relationship's confidence and evidence from `graph` and
    `file_edges` alone — never from anything the model wrote.

    `candidate_components`, when given, also drives a warn-only coverage diagnostic (see the end of this
    function): it never adds, merges or rejects a relationship, and has no effect on any relationship's
    confidence or evidence."""
    settings = settings or get_settings()
    warnings: list[str] = []
    known = frozenset(known_paths)
    module_ids = {n.id for n in graph.nodes if n.type == "module"}
    module_paths = {n.path for n in graph.nodes if n.type == "module"}
    edge_lookup = {(e.source, e.target): e for e in graph.edges if e.level == "module"}
    # File-level edges, keyed the same way `GraphNode.id` names a file ("file:<path>"): the precise case a
    # module-level edge structurally cannot show — two components whose code sits in the *same* module (a
    # module never depends on itself) — plus a sharper, single-pair citation wherever a module-level
    # aggregate would otherwise hand back an arbitrary sample from potentially many underlying file pairs.
    file_edge_lookup = {(e.source, e.target): e for e in file_edges if e.level == "file"}

    def path_known(path: str) -> bool:
        norm = path.strip().strip("/")
        if not norm:
            return False
        if norm in known or norm in module_paths:
            return True
        prefix = norm + "/"
        return any(p.startswith(prefix) for p in known)

    def modules_for(paths: Sequence[str]) -> set[str]:
        mods: set[str] = set()
        for path in paths:
            norm = path.strip().strip("/")
            mod = module_of.get(norm)
            if mod:
                mods.add(mod)
                continue
            if f"module:{norm or '.'}" in module_ids:
                mods.add(f"module:{norm or '.'}")
                continue
            prefix = norm + "/"
            mods |= {module_of[fp] for fp in module_of if fp.startswith(prefix)}
        return mods

    # --- groups -------------------------------------------------------------------------------------------
    seen_group_ids: set[str] = set()
    groups: list[Group] = []
    for g in raw.groups:
        if not g.id.strip() or not g.label.strip():
            warnings.append("a group with an empty id or label was dropped")
            continue
        if g.id in seen_group_ids:
            warnings.append(f"duplicate group id {g.id!r} was dropped")
            continue
        if len(groups) >= settings.architecture_diagram_max_groups:
            warnings.append(f"more than {settings.architecture_diagram_max_groups} groups were proposed; the rest were dropped")
            break
        seen_group_ids.add(g.id)
        groups.append(g)

    # --- components -----------------------------------------------------------------------------------------
    seen_component_ids: set[str] = set()
    components: list[Component] = []
    bare_rename_count = 0
    for c in raw.components:
        if not c.id.strip() or not c.label.strip():
            warnings.append("a component with an empty id or label was dropped")
            continue
        if c.id in seen_component_ids:
            warnings.append(f"duplicate component id {c.id!r} was dropped")
            continue
        if len(components) >= settings.architecture_diagram_max_components:
            warnings.append(f"more than {settings.architecture_diagram_max_components} components were proposed; the rest were dropped")
            break
        seen_component_ids.add(c.id)

        group_id = c.group_id if c.group_id in seen_group_ids else None
        if c.group_id and group_id is None:
            warnings.append(f"component {c.id!r} referenced unknown group {c.group_id!r}; left ungrouped")

        valid_paths: list[str] = []
        dropped = 0
        for path in c.source_paths:
            if len(valid_paths) >= settings.architecture_diagram_max_source_paths_per_component:
                dropped += 1
                continue
            if path in valid_paths:
                continue
            if path_known(path):
                valid_paths.append(path)
            else:
                dropped += 1
        if dropped:
            warnings.append(f"component {c.id!r}: {dropped} source path(s) do not exist in the current snapshot and were dropped")

        evidence_basis: EvidenceBasis = "verified_paths" if valid_paths else "inferred"
        if _looks_like_bare_rename(c, valid_paths):
            bare_rename_count += 1

        components.append(
            Component(
                id=c.id, group_id=group_id, label=c.label, description=c.description, kind=c.kind,
                source_paths=valid_paths, evidence_basis=evidence_basis,
            )
        )
    if bare_rename_count:
        warnings.append(
            f"{bare_rename_count} component(s) look like a bare directory rename (near-empty description, label matches "
            "a path segment); the AI may not have identified meaningful functional architecture"
        )

    # --- relationships ------------------------------------------------------------------------------------------
    components_by_id = {c.id: c for c in components}
    seen_relationship_ids: set[str] = set()
    relationships: list[Relationship] = []
    for r in raw.relationships:
        if not r.id.strip() or r.source == r.target:
            warnings.append(f"relationship {r.id!r} was dropped (missing id, or source equals target)")
            continue
        if r.id in seen_relationship_ids:
            warnings.append(f"duplicate relationship id {r.id!r} was dropped")
            continue
        if r.source not in components_by_id or r.target not in components_by_id:
            warnings.append(f"relationship {r.id!r} references an unknown component and was dropped")
            continue
        if len(relationships) >= settings.architecture_diagram_max_relationships:
            warnings.append(f"more than {settings.architecture_diagram_max_relationships} relationships were proposed; the rest were dropped")
            break
        seen_relationship_ids.add(r.id)

        confidence: Confidence = "inferred"
        evidence: list[DiagramEvidence] = []
        if r.kind in IMPORT_VERIFIABLE_KINDS:
            source_paths = sorted(components_by_id[r.source].source_paths)
            target_paths = sorted(components_by_id[r.target].source_paths)
            # File-level first: the only way to verify two components whose code shares one module (a
            # module has no edge to itself), and a sharper single-pair citation otherwise than a
            # module-level edge's sample from potentially many underlying file pairs.
            file_edge = next(
                (
                    file_edge_lookup[(f"file:{s}", f"file:{t}")]
                    for s, t in product(source_paths, target_paths)
                    if (f"file:{s}", f"file:{t}") in file_edge_lookup
                ),
                None,
            )
            edge = file_edge
            if edge is None:
                source_mods = modules_for(components_by_id[r.source].source_paths)
                target_mods = modules_for(components_by_id[r.target].source_paths)
                edge = next((edge_lookup[pair] for pair in product(source_mods, target_mods) if pair in edge_lookup), None)
            if edge is not None:
                confidence = "verified"
                evidence = [
                    DiagramEvidence(path=e.path, start_line=e.start_line, end_line=e.end_line, detail=e.detail)
                    for e in edge.evidence[: settings.architecture_diagram_max_evidence_per_relationship]
                ]
        relationships.append(Relationship(id=r.id, source=r.source, target=r.target, label=r.label, kind=r.kind, confidence=confidence, evidence=evidence))

    # --- candidate-component relationship-coverage diagnostic (warn-only; never invents or rejects a
    # relationship, never affects confidence/evidence for anything that already exists) -----------------------
    if candidate_components:
        path_to_component: dict[str, str] = {}
        for c in components:
            for path in c.source_paths:
                path_to_component.setdefault(path, c.id)
        connected_pairs = {frozenset((r.source, r.target)) for r in relationships}
        missing = 0
        for cand in candidate_components:
            source_component = path_to_component.get(cand.path)
            if source_component is None:
                continue
            for target_path in cand.imports:  # the inverse of `imported_by`; iterating both would double-count
                target_component = path_to_component.get(target_path)
                if target_component is None or target_component == source_component:
                    continue
                if frozenset((source_component, target_component)) not in connected_pairs:
                    missing += 1
        if missing:
            warnings.append(
                f"{missing} real import connection(s) between candidate components are not represented by any "
                "relationship (possible missing workflow coverage)"
            )

    # --- infrastructure coverage diagnostic (warn-only; never adds a component or relationship) ---------------
    if infrastructure:
        own_words = [(c.label, c.description) for c in components]
        absent = [i for i in infrastructure if not is_represented(i, own_words)]
        strong = [i.name for i in absent if i.strength == "runtime_usage"]
        weak = [i.name for i in absent if i.strength != "runtime_usage"]
        if strong:
            warnings.append(
                f"{len(strong)} infrastructure item(s) with runtime-usage evidence in the source are not represented "
                f"by any component: {', '.join(strong[:5])}"
            )
        if weak:
            warnings.append(
                f"{len(weak)} infrastructure hint(s) known only from imports or manifests are not represented "
                f"(weaker evidence): {', '.join(weak[:5])}"
            )

    return ArchitectureDiagram(
        repository_commit_sha=commit_sha,
        generator_version=DIAGRAM_GENERATOR_VERSION,
        groups=groups,
        components=components,
        relationships=relationships,
        warnings=warnings,
    )
