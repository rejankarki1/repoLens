import { ApiError, request } from '@/lib/api/client'

/*
 * Types mirror apps/api/schemas/architecture.py field for field (docs/architecture.md). They are deliberately not
 * re-shaped: the UI renders the backend's graph, it does not define a model of its own.
 */

export interface Evidence {
  path: string
  start_line: number // 1-based, inclusive
  end_line: number
  kind: string // import | reexport | require | lazy_import | manifest | code | path
  detail: string
}

export interface NodeMetrics {
  incoming: number
  outgoing: number
  external_packages: number
  unresolved_imports: number
}

export interface GraphNode {
  id: string // "module:<dir>" | "file:<path>"
  label: string
  type: 'module' | 'file'
  kind: string
  path: string
  language: string | null
  files: string[]
  file_count: number
  files_truncated: boolean
  roles: string[]
  area_ids: string[]
  evidence: Evidence[]
  metrics: NodeMetrics
  cycle_id: string | null
}

export type Confidence = 'verified' | 'heuristic' | 'mixed'

export interface GraphEdge {
  id: string
  source: string
  target: string
  level: 'module' | 'file'
  relationship: string // file: imports | reexports | requires | lazy_import | type_only; module: depends_on
  basis: 'static' | 'aggregated'
  resolution: string
  confidence: Confidence
  count: number
  breakdown: Record<string, number>
  evidence: Evidence[]
  evidence_truncated: boolean
  cycle_id: string | null
}

export interface Area {
  id: string
  kind: string
  label: string
  confidence: 'high' | 'medium' | 'low'
  file_count: number
  module_ids: string[]
  files: string[]
  evidence: Evidence[]
}

export interface Technology {
  id: string
  name: string
  category: string
  evidence: Evidence[]
}

export interface ExternalDependency {
  name: string
  ecosystem: 'python' | 'python_stdlib' | 'npm' | 'node_builtin' | 'url'
  importer_count: number
  evidence: Evidence[]
}

export interface FileExternal {
  file: string
  name: string
  ecosystem: string
  evidence: Evidence
}

export interface UnresolvedImport {
  file: string
  specifier: string
  reason: string // not_found | alias_not_configured | relative_beyond_root | unsupported
  evidence: Evidence
}

export interface Cycle {
  id: string
  level: 'module' | 'file'
  size: number
  nodes: string[] // capped; every member node carries cycle_id
  edge_ids: string[]
  truncated: boolean
}

export interface Stats {
  files_total: number
  code_files: number
  skipped_files: number
  languages: Record<string, number>
  modules: number
  file_edges: number
  module_edges: number
  external_packages: number
  unresolved_imports: number
  non_code_imports: number
  cycles: number
  truncated: boolean
}

export interface Graph {
  commit_sha: string
  analyzer_version: number
  nodes: GraphNode[]
  edges: GraphEdge[]
  areas: Area[]
  technologies: Technology[]
  external_dependencies: ExternalDependency[]
  cycles: Cycle[]
  stats: Stats
  warnings: string[]
}

export type AttemptStatus = 'none' | 'queued' | 'running' | 'completed' | 'failed'

export interface AnalysisStatus {
  repository_id: number
  status: AttemptStatus // the latest attempt
  freshness: 'none' | 'current' | 'stale' // of the stored result
  current_commit_sha: string | null
  analyzed_commit_sha: string | null
  target_commit_sha: string | null
  analyzed_at: string | null
  requested_at: string | null
  error: string | null
  has_result: boolean
}

export interface ArchitectureResponse {
  analysis: AnalysisStatus
  graph: Graph
}

export interface NodeDetail {
  analysis: AnalysisStatus
  node: GraphNode
  members: GraphNode[]
  internal_edges: GraphEdge[]
  outgoing: GraphEdge[]
  incoming: GraphEdge[]
  related_nodes: GraphNode[]
  external_dependencies: FileExternal[]
  unresolved_imports: UnresolvedImport[]
  cycles: Cycle[]
}

export interface QueueResponse {
  analysis: AnalysisStatus
  enqueued: boolean
}

/* --------------------------------- Requests --------------------------------- */

const base = (repoId: string) => `/repositories/${encodeURIComponent(repoId)}/architecture`

export const getArchitectureStatus = (repoId: string) => request<AnalysisStatus>(`${base(repoId)}/status`)

/** The stored graph, or null while no analysis has completed (the API answers 404 until then). */
export async function getArchitecture(repoId: string): Promise<ArchitectureResponse | null> {
  try {
    return await request<ArchitectureResponse>(base(repoId))
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null
    throw err
  }
}

export const getArchitectureNode = (repoId: string, nodeId: string) =>
  request<NodeDetail>(`${base(repoId)}/nodes?id=${encodeURIComponent(nodeId)}`)

/**
 * Asks the backend to analyze the repository's current commit. It is idempotent on the server: while an analysis of
 * the same commit is queued or running, or the stored result is current (without `force`), nothing new is queued.
 */
export const requestArchitectureAnalysis = (repoId: string, force = false) =>
  request<QueueResponse>(`${base(repoId)}${force ? '?force=true' : ''}`, { method: 'POST' })
