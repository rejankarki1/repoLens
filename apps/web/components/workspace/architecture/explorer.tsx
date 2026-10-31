'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { ArrowLeft, ChevronRight, MousePointerClick, Search } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { GraphCanvas } from '@/components/workspace/architecture/canvas'
import {
  FilePanel, LinkPanel, ModuleDetailSummary, ModuleEdgePanel, ModuleSummary, NeighbourPanel, OverviewInsights,
} from '@/components/workspace/architecture/panels'
import {
  EmptyGraphState, ErrorState, FailedState, PendingState, StatusBanners, StatusStrip,
} from '@/components/workspace/architecture/status'
import { requestArchitectureAnalysis, type AnalysisStatus, type Graph, type GraphEdge } from '@/lib/api/architecture'
import { useArchitectureGraph, useArchitectureNode, useArchitectureStatus } from '@/lib/api/hooks'
import {
  applyFilters, autoRequestKey, count, defaultFilters, makeLabeler, matchingNodes, moduleDetailGraph, overviewGraph, shouldAutoRequest,
  type GraphFilters,
} from '@/lib/architecture/model'
import type { Repository } from '@/lib/types'

/** Commits an analysis was already requested for in this browser session (the server is idempotent too). */
const autoRequested = new Set<string>()

function messageOf(err: unknown): string {
  return err instanceof Error ? err.message : 'Something went wrong.'
}

export function ArchitectureExplorer({ repo }: { repo: Repository }) {
  const status = useArchitectureStatus(repo)
  const graphQuery = useArchitectureGraph(repo, status.data)
  const [requestError, setRequestError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const { mutate: refreshStatus } = status

  const request = useCallback(
    async (force: boolean) => {
      setSubmitting(true)
      setRequestError(null)
      try {
        await requestArchitectureAnalysis(repo.id, force)
        await refreshStatus()
      } catch (err) {
        setRequestError(messageOf(err))
      } finally {
        setSubmitting(false)
      }
    },
    [repo.id, refreshStatus],
  )

  // Ask for an analysis once per commit when there is none (or only an older one); never loop on failure.
  const data = status.data
  useEffect(() => {
    if (!data || !shouldAutoRequest(data)) return
    const key = autoRequestKey(data)
    if (autoRequested.has(key)) return
    autoRequested.add(key)
    requestArchitectureAnalysis(repo.id)
      .then(() => refreshStatus())
      .catch((err) => setRequestError(messageOf(err)))
  }, [data, repo.id, refreshStatus])

  if (status.isLoading) return <ExplorerSkeleton />
  if (status.error || !data) return <ErrorState message={messageOf(status.error)} onRetry={() => void refreshStatus()} />

  const graph = graphQuery.data?.graph
  const rerun = () => void request(data.freshness === 'current')

  return (
    <div className="flex h-full min-h-0 flex-col">
      <StatusStrip status={data} onRerun={rerun} rerunning={submitting} />
      <StatusBanners status={data} requestError={requestError} />
      <div className="min-h-0 flex-1">
        {graph ? (
          <ExplorerBody repo={repo} status={data} graph={graph} />
        ) : data.has_result && !graphQuery.error ? (
          <ExplorerSkeleton />
        ) : graphQuery.error ? (
          <ErrorState message={messageOf(graphQuery.error)} onRetry={() => void graphQuery.mutate()} />
        ) : data.status === 'failed' ? (
          <FailedState status={data} onRetry={rerun} retrying={submitting} />
        ) : (
          <PendingState status={data} />
        )}
      </div>
    </div>
  )
}

function ExplorerSkeleton() {
  return (
    <div className="flex h-full flex-col gap-3 p-6" aria-busy="true" aria-label="Loading architecture">
      <Skeleton className="h-8 w-72" />
      <Skeleton className="min-h-64 flex-1" />
    </div>
  )
}

type Selection = { type: 'node' | 'edge'; id: string } | null

function ExplorerBody({ repo, status, graph }: { repo: Repository; status: AnalysisStatus; graph: Graph }) {
  const searchParams = useSearchParams()
  const drilled = searchParams.get('module')
  const file = searchParams.get('file')
  // Selection belongs to one view of one analysis: remount (and so reset it) when either changes.
  const viewKey = `${graph.commit_sha}:${status.analyzed_at ?? ''}:${drilled ?? 'overview'}`
  return <View key={viewKey} repo={repo} status={status} graph={graph} drilledId={drilled} initialFile={drilled ? file : null} viewKey={viewKey} />
}

function View({
  repo, status, graph, drilledId, initialFile, viewKey,
}: {
  repo: Repository
  status: AnalysisStatus
  graph: Graph
  drilledId: string | null
  /** A file to select when the module opens (a link from the Code Explorer or an answer). */
  initialFile: string | null
  viewKey: string
}) {
  const router = useRouter()
  const [selection, setSelection] = useState<Selection>(() => (initialFile ? { type: 'node', id: `file:${initialFile}` } : null))
  const [override, setOverride] = useState<Partial<GraphFilters>>({})
  const labelOf = useMemo(() => makeLabeler(graph), [graph])
  const base = `/repos/${repo.id}/graph`
  const goTo = useCallback(
    (moduleId: string | null) => router.push(moduleId ? `${base}?module=${encodeURIComponent(moduleId)}` : base, { scroll: false }),
    [router, base],
  )

  const moduleExists = drilledId === null || graph.nodes.some((n) => n.id === drilledId)
  const detail = useArchitectureNode(repo, moduleExists ? drilledId : null, status)

  const overviewViz = useMemo(() => overviewGraph(graph), [graph])
  const detailViz = useMemo(() => (detail.data ? moduleDetailGraph(detail.data, graph) : null), [detail.data, graph])
  const rawViz = drilledId ? detailViz : overviewViz
  const filters = useMemo<GraphFilters>(() => ({ ...(rawViz ? defaultFilters(rawViz) : { typeOnly: true, neighbours: true, query: '' }), ...override }), [rawViz, override])
  const viz = useMemo(() => (rawViz ? applyFilters(rawViz, filters) : null), [rawViz, filters])
  const matches = useMemo(() => (viz ? matchingNodes(viz, filters.query) : new Set<string>()), [viz, filters.query])
  // a selection the filters have removed is no longer on the canvas, so the panel falls back to the summary
  const shown = selection && viz && (selection.type === 'node' ? viz.nodes.some((n) => n.id === selection.id) : viz.edges.some((e) => e.id === selection.id)) ? selection : null

  const selectNode = useCallback((id: string | null) => setSelection(id ? { type: 'node', id } : null), [])
  const selectEdge = useCallback((id: string | null) => setSelection(id ? { type: 'edge', id } : null), [])
  const openNode = useCallback(
    (id: string) => {
      const target = id.startsWith('neighbor:') ? viz?.nodes.find((n) => n.id === id)?.moduleId : id
      if (target?.startsWith('module:')) goTo(target)
    },
    [goTo, viz],
  )

  if (graph.nodes.length === 0) return <EmptyGraphState warnings={graph.warnings} />

  const drilledNode = drilledId ? graph.nodes.find((n) => n.id === drilledId) : undefined
  const panel = drilledId ? (
    <DetailPanel repo={repo} graph={graph} detail={detail.data} viz={detailViz} selection={shown} labelOf={labelOf} goTo={goTo} onSelect={setSelection} />
  ) : (
    <OverviewPanel repo={repo} graph={graph} selection={shown} labelOf={labelOf} goTo={goTo} onSelect={setSelection} />
  )

  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto lg:overflow-hidden">
      <div className="flex min-h-9 shrink-0 flex-wrap items-center gap-x-2 gap-y-1 border-b border-border px-3 py-1.5 text-xs sm:px-5" aria-label="View">
        {drilledId ? (
          <>
            <Button size="xs" variant="ghost" onClick={() => goTo(null)}>
              <ArrowLeft aria-hidden="true" />
              All modules
            </Button>
            <ChevronRight className="size-3.5 text-muted-foreground" aria-hidden="true" />
            <span className="font-mono font-medium">{drilledNode ? drilledNode.label : drilledId}</span>
            {detailViz && (
              <span className="text-muted-foreground">
                · {count(detail.data?.members.length ?? 0, 'file')} · {count(detail.data?.internal_edges.length ?? 0, 'import')} between them
              </span>
            )}
          </>
        ) : (
          <p className="flex items-center gap-1.5 text-muted-foreground">
            <MousePointerClick className="size-3.5" aria-hidden="true" />
            {count(graph.stats.modules, 'module')} · {count(graph.stats.module_edges, 'dependency', 'dependencies')}. Select a module or arrow; double-click a module (or press
            Enter, then Explore files) to see its files.
          </p>
        )}
        {rawViz && viz && (
          <GraphToolbar
            filters={filters}
            onChange={(next) => setOverride((o) => ({ ...o, ...next }))}
            hiddenEdges={viz.hiddenEdges}
            detail={!!drilledId}
            matches={matches.size}
            onPickFirst={() => {
              const first = [...matches][0]
              if (first) setSelection({ type: 'node', id: first })
            }}
          />
        )}
      </div>

      <div className="flex flex-col lg:min-h-0 lg:flex-1 lg:flex-row">
        <div className="relative h-[62vh] min-h-80 shrink-0 lg:h-auto lg:min-h-0 lg:flex-1">
          {!moduleExists ? (
            <Missing onBack={() => goTo(null)} />
          ) : drilledId && detail.error ? (
            <ErrorState message={messageOf(detail.error)} onRetry={() => void detail.mutate()} />
          ) : viz ? (
            <GraphCanvas
              graph={viz}
              graphKey={`${viewKey}:${filters.typeOnly}:${filters.neighbours}`}
              matchIds={matches}
              selectedNodeId={shown?.type === 'node' ? shown.id : null}
              selectedEdgeId={shown?.type === 'edge' ? shown.id : null}
              onSelectNode={selectNode}
              onSelectEdge={selectEdge}
              onOpenNode={openNode}
            />
          ) : (
            <div className="p-6" aria-busy="true">
              <Skeleton className="h-full min-h-64 w-full" />
            </div>
          )}
        </div>
        <aside className="shrink-0 border-t border-border bg-sidebar/50 lg:w-[26rem] lg:overflow-y-auto lg:border-t-0 lg:border-l" aria-label="Details">
          {panel}
        </aside>
      </div>
    </div>
  )
}

function GraphToolbar({
  filters, onChange, hiddenEdges, detail, matches, onPickFirst,
}: {
  filters: GraphFilters
  onChange: (next: Partial<GraphFilters>) => void
  hiddenEdges: number
  detail: boolean
  matches: number
  onPickFirst: () => void
}) {
  const box = 'flex items-center gap-1.5 text-muted-foreground hover:text-foreground'
  return (
    <div className="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1" role="group" aria-label="Graph filters">
      <label className="flex h-7 items-center gap-1.5 rounded-md border border-border bg-card px-2 focus-within:border-ring">
        <Search className="size-3.5 text-muted-foreground" aria-hidden="true" />
        <span className="sr-only">{detail ? 'Find a file' : 'Find a module'}</span>
        <input
          value={filters.query}
          onChange={(e) => onChange({ query: e.target.value })}
          onKeyDown={(e) => e.key === 'Enter' && onPickFirst()}
          placeholder={detail ? 'Find a file…' : 'Find a module…'}
          className="w-32 bg-transparent text-xs outline-none placeholder:text-muted-foreground sm:w-40"
        />
        {filters.query.trim() && (
          <span className="text-[11px] text-muted-foreground tabular-nums" aria-live="polite">
            {matches === 0 ? 'no match' : count(matches, 'match', 'matches')}
          </span>
        )}
      </label>
      <label className={box}>
        <input type="checkbox" className="accent-primary" checked={filters.typeOnly} onChange={(e) => onChange({ typeOnly: e.target.checked })} />
        Type-only imports
      </label>
      {detail && (
        <label className={box}>
          <input type="checkbox" className="accent-primary" checked={filters.neighbours} onChange={(e) => onChange({ neighbours: e.target.checked })} />
          Other modules
        </label>
      )}
      {hiddenEdges > 0 && <span className="text-[11px] text-muted-foreground">{count(hiddenEdges, 'arrow')} hidden</span>}
    </div>
  )
}

function Missing({ onBack }: { onBack: () => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center text-sm text-muted-foreground">
      <p>That module is not in the current analysis. The repository may have changed since the link was made.</p>
      <Button size="sm" variant="outline" onClick={onBack}>
        Back to all modules
      </Button>
    </div>
  )
}

interface PanelProps {
  repo: Repository
  graph: Graph
  selection: Selection
  labelOf: (id: string) => string
  goTo: (moduleId: string | null) => void
  onSelect: (s: Selection) => void
}

function OverviewPanel({ repo, graph, selection, labelOf, goTo, onSelect }: PanelProps) {
  if (selection?.type === 'edge') {
    const edge = graph.edges.find((e) => e.id === selection.id)
    if (edge) return <ModuleEdgePanel repoId={repo.id} edges={[edge]} labelOf={labelOf} onExplore={goTo} />
  }
  if (selection?.type === 'node') {
    const node = graph.nodes.find((n) => n.id === selection.id)
    if (node) {
      return (
        <ModuleSummary
          repoId={repo.id}
          graph={graph}
          node={node}
          onPickEdge={(e: GraphEdge) => onSelect({ type: 'edge', id: e.id })}
          onExplore={() => goTo(node.id)}
        />
      )
    }
  }
  return <OverviewInsights repoId={repo.id} graph={graph} onSelectModule={(id) => onSelect({ type: 'node', id })} />
}

function DetailPanel({
  repo, graph, detail, viz, selection, labelOf, goTo, onSelect,
}: PanelProps & { detail: ReturnType<typeof useArchitectureNode>['data']; viz: ReturnType<typeof moduleDetailGraph> | null }) {
  if (!detail || !viz) {
    return (
      <div className="flex flex-col gap-3 p-4" aria-busy="true">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    )
  }
  const back = () => onSelect(null)
  if (selection?.type === 'edge') {
    const edge = viz.edges.find((e) => e.id === selection.id)
    if (edge) {
      const merged = edge.edges.length > 1 || edge.id.startsWith('link:')
      return (
        <LinkPanel
          repoId={repo.id}
          edges={edge.edges}
          labelOf={labelOf}
          title={`${labelOf(edge.source)} → ${labelOf(edge.target)}`}
          note={merged ? 'All import statements between these two, from the source files.' : 'The import statement behind this dependency.'}
        />
      )
    }
  }
  if (selection?.type === 'node') {
    const node = viz.nodes.find((n) => n.id === selection.id)
    if (node?.type === 'file' && node.node) return <FilePanel repoId={repo.id} detail={detail} node={node.node} labelOf={labelOf} />
    if (node?.type === 'neighbor') {
      return <NeighbourPanel repoId={repo.id} node={node} vizEdges={viz.edges} labelOf={labelOf} onOpen={node.moduleId ? () => goTo(node.moduleId) : undefined} />
    }
  }
  return (
    <ModuleDetailSummary
      repoId={repo.id}
      graph={graph}
      detail={detail}
      viz={viz}
      onBack={() => goTo(null)}
      onPickEdge={(e) => {
        const other = e.source === detail.node.id ? e.target : e.source
        if (viz.nodes.some((n) => n.id === `neighbor:${other}`)) onSelect({ type: 'node', id: `neighbor:${other}` })
        else back()
      }}
    />
  )
}
