"""Architecture Diagram V2 types: an AI-proposed functional-component graph, sealed by backend validation.

Two layers, deliberately kept apart:

* `Raw*` is exactly what the model is allowed to say: components and relationships with no claim of
  verification anywhere in sight. The model is never even given a place to write "verified" (see
  `services/architecture_diagram/validate.py`) because its say-so must never be trusted, the same way
  `services/rag.py` never trusts a model's own citations.
* The plain (non-`Raw`) types are the backend-sealed result: `Component.evidence_basis` and
  `Relationship.confidence`/`evidence` are computed by `validate.py` from the repository's real file
  snapshot and the deterministic import graph (`schemas/architecture.py`), never copied from the model.

`ArchitectureDiagram` is the provider-independent result: stored as-is in `architecture_diagrams.diagram`
(see `services/architecture_diagram/store.py`), returned as-is by the API, and compiled to Mermaid entirely
client-side (`apps/web/lib/architecture-diagram/mermaid.ts`) so a theme change never needs a new backend call.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

DIAGRAM_GENERATOR_VERSION = 1

ComponentKind = Literal[
    "service", "api", "worker", "job", "ui", "page", "database", "store",
    "external_service", "library", "cli", "pipeline", "other",
]
RelationshipKind = Literal["imports", "sync_call", "async_call", "queue", "db_read", "db_write", "renders", "other"]
Confidence = Literal["verified", "inferred"]
EvidenceBasis = Literal["verified_paths", "inferred"]

# Only this relationship kind can ever be marked "verified": it is the one claim the static import graph
# can actually support. A sync/async call, a queue hop, or a database read/write is a runtime behavior that
# static analysis cannot see, so an import edge between the same two modules is never enough evidence for
# it (see validate.py). Kept here, not in validate.py, so the rule is visible next to the vocabulary it
# constrains.
IMPORT_VERIFIABLE_KINDS = frozenset({"imports"})

# `kind` is a free-text classification, not a verification claim, so normalizing a model's near-miss
# wording here is safe in a way normalizing `confidence`/`evidence` would not be (those are never even
# exposed to the model — see the module docstring). A real model observed in practice: "component" for a
# React component instead of "ui". Anything not recognized and not a listed synonym becomes "other" rather
# than failing the whole response over one field.
_COMPONENT_KIND_SYNONYMS: dict[str, ComponentKind] = {
    "component": "ui", "frontend": "ui", "view": "ui", "widget": "ui",
    "controller": "api", "route": "api", "router": "api", "endpoint": "api",
    "background_job": "job", "task": "job", "scheduler": "job",
    "model_layer": "model", "db": "database", "data_model": "model", "orm": "model",
    "cache": "store", "data_store": "store", "repository_layer": "store",
    "sdk": "library", "package": "library", "module": "library",
    "script": "cli", "tool": "cli", "command": "cli",
    "integration": "external_service", "third_party": "external_service", "provider": "external_service",
}
_RELATIONSHIP_KIND_SYNONYMS: dict[str, RelationshipKind] = {
    "http_call": "sync_call", "http_request": "sync_call", "rest_call": "sync_call", "api_call": "sync_call",
    "calls": "sync_call", "event": "async_call", "message": "async_call", "publishes": "async_call",
    "subscribes": "async_call", "job_queue": "queue", "enqueues": "queue", "reads": "db_read", "writes": "db_write",
    "persists": "db_write", "reexports": "imports", "requires": "imports", "uses": "other", "depends_on": "other",
}


def _normalize_kind(literal: type, synonyms: dict[str, str]):
    """A `BeforeValidator` for a `Literal` kind field: pass through a recognized value, map a known
    synonym, else fall back to `"other"` — never raise over this one field."""
    allowed = set(get_args(literal))

    def _validator(value: object) -> str:
        text = re.sub(r"[\s-]+", "_", str(value).strip().lower())
        if text in allowed:
            return text
        return synonyms.get(text, "other")

    return _validator


def _clip(max_len: int):
    """A `BeforeValidator` that trims to `max_len` instead of rejecting: the model's output is a proposal,
    so an over-long label should be shortened, not treated as a validation failure that discards an
    otherwise-usable component or relationship."""

    def _validator(value: object) -> str:
        text = str(value).strip()
        return text if len(text) <= max_len else text[: max_len - 1].rstrip() + "…"

    return _validator


Id = Annotated[str, BeforeValidator(_clip(80))]
Label = Annotated[str, BeforeValidator(_clip(120))]
Description = Annotated[str, BeforeValidator(_clip(600))]
RelationshipLabel = Annotated[str, BeforeValidator(_clip(160))]
SourcePath = Annotated[str, BeforeValidator(_clip(300))]
NormalizedComponentKind = Annotated[ComponentKind, BeforeValidator(_normalize_kind(ComponentKind, _COMPONENT_KIND_SYNONYMS))]
NormalizedRelationshipKind = Annotated[RelationshipKind, BeforeValidator(_normalize_kind(RelationshipKind, _RELATIONSHIP_KIND_SYNONYMS))]


class DiagramEvidence(BaseModel):
    """A pointer at real source, always built by the backend from the deterministic graph's own evidence
    (`schemas/architecture.py:Evidence`); never written by the model."""

    path: str
    start_line: int
    end_line: int
    detail: str


class Group(BaseModel):
    """A functional grouping of components (e.g. "Authentication", "Ingestion Pipeline"). No path or
    evidence of its own: it exists to organize the diagram, not to make a claim about the code."""

    model_config = ConfigDict(extra="ignore")
    id: Id
    label: Label
    description: Description = ""


class RawComponent(BaseModel):
    """A component exactly as the model proposed it: no `evidence_basis` field exists here, so the model
    cannot claim its paths are verified."""

    model_config = ConfigDict(extra="ignore")
    id: Id
    group_id: Id | None = None
    label: Label
    description: Description = ""
    kind: NormalizedComponentKind
    source_paths: list[SourcePath] = Field(default_factory=list)


class Component(RawComponent):
    """`RawComponent` plus the one fact only the backend can establish: whether `source_paths` (after
    filtering to paths that exist in the current snapshot) is non-empty."""

    evidence_basis: EvidenceBasis


class RawRelationship(BaseModel):
    """A relationship exactly as the model proposed it. There is deliberately no `confidence` or
    `evidence` field: the model is never given a place to assert verification (see module docstring)."""

    model_config = ConfigDict(extra="ignore")
    id: Id
    source: Id
    target: Id
    label: RelationshipLabel
    kind: NormalizedRelationshipKind


class Relationship(RawRelationship):
    """`RawRelationship` plus backend-computed confidence and evidence.

    `confidence="verified"` is only ever set for `kind="imports"` and only when a real module-level
    `depends_on` edge connects the two components' modules in the stated direction (`validate.py`).
    Every other kind, however plausible, is always `"inferred"` with empty `evidence`: static import
    analysis has no way to see an HTTP call, a queue hop, or a database read/write actually happening.
    """

    confidence: Confidence
    evidence: list[DiagramEvidence] = Field(default_factory=list)


class RawArchitectureDiagram(BaseModel):
    """The strict parse target for the model's JSON response. Unknown keys are ignored (models sometimes
    add a stray field); anything shaped wrong fails to parse rather than being silently coerced."""

    model_config = ConfigDict(extra="ignore")
    groups: list[Group] = Field(default_factory=list)
    components: list[RawComponent] = Field(default_factory=list)
    relationships: list[RawRelationship] = Field(default_factory=list)


class ArchitectureDiagram(BaseModel):
    """The sealed, provider-independent result: what `validate.py` produces from a `RawArchitectureDiagram`
    plus the repository's real snapshot and deterministic graph. This is what a later phase compiles to
    Mermaid; nothing in it is a model's unverified claim."""

    repository_commit_sha: str
    generator_version: int = DIAGRAM_GENERATOR_VERSION
    groups: list[Group] = Field(default_factory=list)
    components: list[Component] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DiagramStatus(BaseModel):
    """Mirrors `schemas/architecture.py::AnalysisStatus` exactly, one layer up: `status` is the latest
    generation *attempt*; `freshness` is whether the stored *result* still matches both the repository's
    current commit and the deterministic analysis it was built from."""

    repository_id: int
    status: Literal["none", "queued", "running", "completed", "failed"]
    freshness: Literal["none", "current", "stale"]
    current_commit_sha: str | None
    generated_commit_sha: str | None
    target_commit_sha: str | None
    generated_at: datetime | None
    requested_at: datetime | None
    error: str | None
    has_result: bool
    model: str | None  # provider/model that produced the stored result, for diagnostics


class DiagramResponse(BaseModel):
    status: DiagramStatus
    diagram: ArchitectureDiagram


class DiagramQueueResponse(BaseModel):
    status: DiagramStatus
    enqueued: bool  # false when the stored result is already current or a generation is already running
