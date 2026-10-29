/**
 * View-model helpers for the architecture explorer. Pure functions over the Phase 7A API types; nothing here
 * creates data the backend did not return (every path, line range and edge shown comes from the response).
 */
import type { AnalysisStatus, Cycle, Evidence, Graph, GraphEdge, GraphNode, NodeDetail } from '@/lib/api/architecture'

export const STATUS_POLL_MS = 2_000

/* ---------------------------------- Status ---------------------------------- */

export const isActive = (status: AnalysisStatus | undefined) => status?.status === 'queued' || status?.status === 'running'

/**
 * Whether the explorer should ask for an analysis on its own: when the repository is ingested and there is no
 * current result and nothing is queued, running, or has just failed (a failure is retried by the user, never in a loop).
 */
export function shouldAutoRequest(status: AnalysisStatus): boolean {
  if (!status.current_commit_sha) return false
  if (status.status === 'queued' || status.status === 'running' || status.status === 'failed') return false
  return status.freshness !== 'current'
}

/** One automatic request per repository and commit, however often the view remounts. */
export const autoRequestKey = (status: AnalysisStatus) => `${status.repository_id}:${status.current_commit_sha}`

export const shortSha = (sha: string | null | undefined) => (sha ? sha.slice(0, 7) : '')

/* --------------------------------- Evidence --------------------------------- */

/** Evidence kinds that are an actual import statement, as opposed to the lines a classification rests on. */
const IMPORT_KINDS = new Set(['import', 'reexport', 'require', 'lazy_import'])
export const isImportEvidence = (e: Pick<Evidence, 'kind'>) => IMPORT_KINDS.has(e.kind)

export function formatLines(start: number, end: number): string {
  return start === end ? `${start}` : `${start}-${end}`
}

/** Code Explorer deep link for a piece of evidence. A `path` kind has no meaningful line, so none is highlighted. */
export function codeLink(repoId: string, e: Pick<Evidence, 'path' | 'start_line' | 'end_line' | 'kind'>): string {
  const lines = e.kind === 'path' ? '' : `&lines=${formatLines(e.start_line, e.end_line)}`
  return `/repos/${repoId}?tab=code&file=${encodeURIComponent(e.path)}${lines}`
}

export const fileLink = (repoId: string, path: string) => `/repos/${repoId}?tab=code&file=${encodeURIComponent(path)}`

/* ---------------------------------- Kinds ----------------------------------- */

export type Tone = 'api' | 'frontend' | 'database' | 'workers' | 'serverless' | 'tests' | 'entry' | 'plain'

const KIND_TONE: Record<string, [Tone, string]> = {
  api: ['api', 'API'],
  route: ['api', 'Route'],
  frontend: ['frontend', 'Frontend'],
  page: ['frontend', 'Page'],
  component: ['frontend', 'Component'],
  database: ['database', 'Database'],
  model: ['database', 'Model'],
  migration: ['database', 'Migration'],
  workers: ['workers', 'Workers'],
  job: ['workers', 'Job'],
  serverless: ['serverless', 'Serverless'],
  edge_function: ['serverless', 'Edge function'],
  tests: ['tests', 'Tests'],
  test: ['tests', 'Test'],
  entrypoint: ['entry', 'Entry point'],
}

/** Tone and label of a node kind; `source` and `directory` are unclassified and get no badge. */
export function kindInfo(kind: string): { tone: Tone; label: string | null } {
  const found = KIND_TONE[kind]
  return found ? { tone: found[0], label: found[1] } : { tone: 'plain', label: null }
}

/* ------------------------------- Graph shaping ------------------------------- */

export type VizKind = 'module' | 'file' | 'neighbor'

export interface VizNode {
  id: string
  label: string
  sublabel: string | null
  type: VizKind
  kind: string
  fileCount: number
  incoming: number
  outgoing: number
  unresolved: number
  inCycle: boolean
  /** The backend node this is drawn from (a neighbour stands for a whole other module). */
  node: GraphNode | null
  /** For a neighbour: the module it represents, when it is one of the overview's modules. */
  moduleId: string | null
}

export interface VizEdge {
  id: string
  source: string
  target: string
  count: number
  inCycle: boolean
  /** The backend edges drawn as this one (several for a neighbour link, which merges a file's imports of one module). */
  edges: GraphEdge[]
}

export interface VizGraph {
  nodes: VizNode[]
  edges: VizEdge[]
  /** Files left out of the picture because nothing connects them to anything shown. */
  hidden: GraphNode[]
}

const moduleLabel = (n: GraphNode) => (n.label === '(root)' ? 'repository root' : n.label)

export function overviewGraph(graph: Graph): VizGraph {
  return {
    nodes: graph.nodes.map((n) => ({
      id: n.id,
      label: moduleLabel(n),
      sublabel: null,
      type: 'module' as const,
      kind: n.kind,
      fileCount: n.file_count,
      incoming: n.metrics.incoming,
      outgoing: n.metrics.outgoing,
      unresolved: n.metrics.unresolved_imports,
      inCycle: n.cycle_id !== null,
      node: n,
      moduleId: n.id,
    })),
    edges: graph.edges.map((e) => ({ id: e.id, source: e.source, target: e.target, count: e.count, inCycle: e.cycle_id !== null, edges: [e] })),
    hidden: [],
  }
}

/** Which overview module a file path belongs to (from the overview's per-module file lists). */
export function moduleOfPath(graph: Graph): Map<string, GraphNode> {
  const map = new Map<string, GraphNode>()
  for (const m of graph.nodes) for (const f of m.files) map.set(f, m)
  return map
}

const dirname = (path: string) => (path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '')

/**
 * The file-level picture of one module: its files and the imports between them, plus one compact "neighbour" node per
 * other module it talks to. Files with no connection to anything shown are returned in `hidden` instead of drawn.
 */
export function moduleDetailGraph(detail: NodeDetail, graph: Graph): VizGraph {
  const owner = moduleOfPath(graph)
  const members = new Set(detail.members.map((m) => m.id))
  const neighbours = new Map<string, VizNode>()
  const neighbourFor = (file: GraphNode): VizNode => {
    const owning = owner.get(file.path)
    const id = owning ? `neighbor:${owning.id}` : `neighbor:dir:${dirname(file.path)}`
    let node = neighbours.get(id)
    if (!node) {
      node = {
        id,
        label: owning ? moduleLabel(owning) : dirname(file.path) || 'repository root',
        sublabel: null,
        type: 'neighbor',
        kind: owning?.kind ?? 'directory',
        fileCount: owning?.file_count ?? 0,
        incoming: 0,
        outgoing: 0,
        unresolved: 0,
        inCycle: false,
        node: owning ?? null,
        moduleId: owning?.id ?? null,
      }
      neighbours.set(id, node)
    }
    return node
  }
  const relatedById = new Map(detail.related_nodes.map((n) => [n.id, n]))

  const edges: VizEdge[] = detail.internal_edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    count: e.count,
    inCycle: e.cycle_id !== null,
    edges: [e],
  }))
  const merged = new Map<string, VizEdge>()
  const addMerged = (e: GraphEdge, member: string, other: GraphNode, outgoing: boolean) => {
    const stub = neighbourFor(other)
    const source = outgoing ? member : stub.id
    const target = outgoing ? stub.id : member
    const id = `link:${source}->${target}`
    const existing = merged.get(id)
    if (existing) {
      existing.count += e.count
      existing.edges.push(e)
      existing.inCycle = existing.inCycle || e.cycle_id !== null
    } else {
      merged.set(id, { id, source, target, count: e.count, inCycle: e.cycle_id !== null, edges: [e] })
    }
  }
  for (const e of detail.outgoing) {
    const other = relatedById.get(e.target)
    if (other && members.has(e.source)) addMerged(e, e.source, other, true)
  }
  for (const e of detail.incoming) {
    const other = relatedById.get(e.source)
    if (other && members.has(e.target)) addMerged(e, e.target, other, false)
  }
  edges.push(...merged.values())

  const touched = new Set(edges.flatMap((e) => [e.source, e.target]))
  const nodes: VizNode[] = detail.members
    .filter((m) => touched.has(m.id))
    .map((m) => ({
      id: m.id,
      label: m.label,
      sublabel: dirname(m.path) || null,
      type: 'file' as const,
      kind: m.kind,
      fileCount: 1,
      incoming: m.metrics.incoming,
      outgoing: m.metrics.outgoing,
      unresolved: m.metrics.unresolved_imports,
      inCycle: m.cycle_id !== null,
      node: m,
      moduleId: null,
    }))
  for (const stub of neighbours.values()) {
    for (const e of merged.values()) {
      if (e.source === stub.id) stub.incoming += 1
      else if (e.target === stub.id) stub.outgoing += 1
    }
    nodes.push(stub)
  }
  return { nodes, edges, hidden: detail.members.filter((m) => !touched.has(m.id)) }
}

/** Ids of the nodes and edges touching `selected` (used to emphasize a selection and dim the rest). */
export function neighbourhood(graph: VizGraph, selected: string): { nodes: Set<string>; edges: Set<string> } {
  const nodes = new Set([selected])
  const edges = new Set<string>()
  for (const e of graph.edges) {
    if (e.source === selected || e.target === selected) {
      edges.add(e.id)
      nodes.add(e.source)
      nodes.add(e.target)
    }
  }
  return { nodes, edges }
}

/** Overview edges leaving (`out`) or entering a module, strongest dependency first. */
export function moduleRelations(graph: Graph, moduleId: string): { out: GraphEdge[]; in: GraphEdge[] } {
  const strongest = (a: GraphEdge, b: GraphEdge) => b.count - a.count || (a.id < b.id ? -1 : 1)
  return {
    out: graph.edges.filter((e) => e.source === moduleId).sort(strongest),
    in: graph.edges.filter((e) => e.target === moduleId).sort(strongest),
  }
}

export function findCycle(cycles: Cycle[][], id: string | null): Cycle | null {
  if (!id) return null
  for (const list of cycles) {
    const found = list.find((c) => c.id === id)
    if (found) return found
  }
  return null
}

export const RELATIONSHIP_LABEL: Record<string, string> = {
  imports: 'imports',
  reexports: 're-exports',
  requires: 'require()',
  lazy_import: 'lazy import',
  type_only: 'type-only import',
}

/** Human label for a node id, from the overview's modules; file ids show their path. */
export function makeLabeler(graph: Graph): (id: string) => string {
  const modules = new Map(graph.nodes.map((n) => [n.id, moduleLabel(n)]))
  return (id) => {
    if (id.startsWith('neighbor:dir:')) return id.slice('neighbor:dir:'.length) || 'repository root'
    const key = id.startsWith('neighbor:') ? id.slice('neighbor:'.length) : id
    return modules.get(key) ?? (key.startsWith('file:') ? key.slice(5) : key.replace(/^module:/, ''))
  }
}

export const UNRESOLVED_REASON: Record<string, string> = {
  not_found: 'no such file in this snapshot',
  alias_not_configured: 'path alias is not configured',
  relative_beyond_root: 'relative import above the repository root',
  unsupported: 'unsupported import form',
}

/** Everything an import-ecosystem filter needs: standard-library and runtime built-ins are not dependencies to install. */
export const isInstallable = (ecosystem: string) => ecosystem !== 'python_stdlib' && ecosystem !== 'node_builtin'

/** "1 file", "2 files". */
export const count = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`
