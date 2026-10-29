"""Architecture graph types: stored in the database as JSON and returned by the API as-is.

Evidence model. Every node, edge and area points at the code that justifies it:

* a static import edge carries the import statement(s), as file + 1-based inclusive line range;
* a node's `kind` (route, model, job, ...) and an area carry the lines that matched, or the manifest line that
  declares the framework;
* module edges are *aggregated* from file edges and carry a sample of the underlying file-level evidence.

Relationships are only ever extracted from source text (`basis: "static"`) or summed from those
(`"aggregated"`). Areas and node kinds are *classifications*: they say what a file appears to be, with the
evidence that led there, and never create edges.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["verified", "heuristic", "mixed"]


class Evidence(BaseModel):
    path: str
    start_line: int  # 1-based, inclusive
    end_line: int
    kind: str  # import | reexport | require | lazy_import | manifest | code | path
    detail: str  # the statement or matched line (trimmed), or a short explanation


class NodeMetrics(BaseModel):
    incoming: int = 0  # distinct nodes of the same level that depend on this one
    outgoing: int = 0  # distinct nodes of the same level this one depends on
    external_packages: int = 0
    unresolved_imports: int = 0


class GraphNode(BaseModel):
    id: str  # "module:<dir>" or "file:<path>"; stable for a given path
    label: str
    type: Literal["module", "file"]
    kind: str  # file: source | test | route | model | migration | job | component | page | edge_function | entrypoint; module: directory or the dominant area kind
    path: str  # repository-relative; "." for the repository root module
    language: str | None = None
    files: list[str] = Field(default_factory=list)  # module: member files (capped); file: itself
    file_count: int = 0
    files_truncated: bool = False
    roles: list[str] = Field(default_factory=list)
    area_ids: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    metrics: NodeMetrics = Field(default_factory=NodeMetrics)
    cycle_id: str | None = None  # the circular-import group this node belongs to, if any


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    level: Literal["module", "file"]
    relationship: str  # file: imports | reexports | requires | lazy_import | type_only; module: depends_on
    basis: Literal["static", "aggregated"]
    resolution: str  # relative | absolute | submodule | ancestor_dir | alias | base_url | workspace; module edges: aggregated
    confidence: Confidence  # verified: unique target found by the language's resolution rules; heuristic: a guess among the rules
    count: int  # import statements (file edge) or file edges (module edge)
    breakdown: dict[str, int] = Field(default_factory=dict)  # module edges: relationship -> statements
    evidence: list[Evidence] = Field(default_factory=list)
    evidence_truncated: bool = False
    cycle_id: str | None = None  # set for an eager import inside a circular-import group


class Area(BaseModel):
    id: str  # "area:<kind>"
    kind: str  # api | frontend | database | workers | serverless | tests
    label: str
    confidence: Literal["high", "medium", "low"]
    file_count: int
    module_ids: list[str]
    files: list[str]  # capped
    evidence: list[Evidence]


class Technology(BaseModel):
    id: str
    name: str
    category: str
    evidence: list[Evidence]  # a declared dependency and/or imports in code; never a directory name


class ExternalDependency(BaseModel):
    name: str
    ecosystem: Literal["python", "python_stdlib", "npm", "node_builtin", "url"]
    importer_count: int
    evidence: list[Evidence]


class FileExternal(BaseModel):
    file: str
    name: str
    ecosystem: str
    evidence: Evidence


class UnresolvedImport(BaseModel):
    file: str
    specifier: str
    reason: str  # not_found | alias_not_configured | relative_beyond_root | unsupported
    evidence: Evidence


class Cycle(BaseModel):
    """A group of nodes that import each other, directly or not (a strongly connected component).

    Only eager relationships count (imports, re-exports, require); lazy and type-only imports cannot make a
    load-time cycle. `nodes` and `edge_ids` list at most a few dozen entries; `size` is the real node count, and
    every member node and edge carries this cycle's id in `cycle_id`.
    """

    id: str
    level: Literal["module", "file"]
    size: int
    nodes: list[str]  # node ids, sorted, capped
    edge_ids: list[str]  # capped
    truncated: bool = False


class Stats(BaseModel):
    files_total: int
    code_files: int  # supported-language files analyzed (graph nodes)
    skipped_files: int  # supported-language files over the analysis limit or unparseable
    languages: dict[str, int]
    modules: int
    file_edges: int
    module_edges: int
    external_packages: int
    unresolved_imports: int
    non_code_imports: int  # imports of existing non-code files (css, json, images)
    cycles: int
    truncated: bool


class Graph(BaseModel):
    """High-level view: modules and the aggregated dependencies between them."""

    commit_sha: str
    analyzer_version: int
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    areas: list[Area]
    technologies: list[Technology]
    external_dependencies: list[ExternalDependency]  # most-imported first (capped; the full list is per file)
    cycles: list[Cycle]
    stats: Stats
    warnings: list[str]


class AnalysisStatus(BaseModel):
    repository_id: int
    status: Literal["none", "queued", "running", "completed", "failed"]  # the latest attempt
    freshness: Literal["none", "current", "stale"]  # of the stored result, against the repository's ingested commit
    current_commit_sha: str | None  # what the repository is ingested at now
    analyzed_commit_sha: str | None  # what the stored result describes
    target_commit_sha: str | None  # what the latest attempt analyzes
    analyzed_at: datetime | None
    requested_at: datetime | None
    error: str | None
    has_result: bool


class ArchitectureResponse(BaseModel):
    analysis: AnalysisStatus
    graph: Graph


class NodeDetailResponse(BaseModel):
    """File-level detail of one module (its files and dependencies) or one file."""

    analysis: AnalysisStatus
    node: GraphNode
    members: list[GraphNode]  # module: its file nodes; file: []
    internal_edges: list[GraphEdge]  # module: file edges among its files; file: []
    outgoing: list[GraphEdge]  # file edges leaving the module / the file
    incoming: list[GraphEdge]  # file edges entering the module / the file
    related_nodes: list[GraphNode]  # file nodes on the other end of outgoing/incoming
    external_dependencies: list[FileExternal]
    unresolved_imports: list[UnresolvedImport]
    cycles: list[Cycle]


class QueueResponse(BaseModel):
    analysis: AnalysisStatus
    enqueued: bool  # false when the stored result is already current or an analysis is already running
