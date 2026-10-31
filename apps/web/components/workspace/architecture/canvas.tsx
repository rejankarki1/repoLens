'use client'

import '@xyflow/react/dist/style.css'

import { useMemo, useState, type KeyboardEvent } from 'react'
import { useTheme } from 'next-themes'
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Panel,
  Position,
  ReactFlow,
  useReactFlow,
  useStore,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import { ArrowLeft, ArrowRight, Maximize, RefreshCcw, TriangleAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { KindBadge, TONE_CLASSES } from '@/components/workspace/architecture/evidence'
import { layoutGraph } from '@/lib/architecture/layout'
import { count, kindInfo, neighbourhood, type VizEdge, type VizGraph, type VizNode } from '@/lib/architecture/model'
import { cn } from '@/lib/utils'

const SIZE = {
  module: { width: 216, height: 88 },
  file: { width: 216, height: 66 },
  neighbor: { width: 196, height: 58 },
} as const

type NodeData = {
  viz: VizNode
  dimmed: boolean
  onSelect: (id: string) => void
  onOpen: (id: string) => void
}
type CardNode = Node<NodeData, 'card'>

function Card({ data, selected }: NodeProps<CardNode>) {
  const { viz, dimmed, onSelect, onOpen } = data
  const { tone } = kindInfo(viz.kind)
  const canOpen = viz.type === 'module' || (viz.type === 'neighbor' && viz.moduleId !== null)
  // Zoomed far out the detail is unreadable anyway, so show just a large name (semantic zoom).
  const farOut = useStore((state) => state.transform[2] < 0.6)
  // Lead with the module's own name; its parent path is secondary.
  const slash = viz.type === 'file' ? -1 : viz.label.lastIndexOf('/')
  const name = slash >= 0 ? viz.label.slice(slash + 1) : viz.label
  const parent = slash >= 0 ? viz.label.slice(0, slash) : viz.sublabel

  function handleKey(e: KeyboardEvent) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onSelect(viz.id)
    } else if ((e.key === 'ArrowRight' || e.key === 'o') && canOpen) {
      e.preventDefault()
      onOpen(viz.id)
    }
  }

  const summary =
    viz.type === 'neighbor'
      ? `${viz.label}, another module`
      : `${viz.label}, ${viz.type === 'module' ? `${count(viz.fileCount, 'file')}, ` : ''}depends on ${viz.outgoing}, used by ${viz.incoming}`

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={summary}
      aria-pressed={selected}
      title={viz.node?.path ?? viz.label}
      onClick={() => onSelect(viz.id)}
      onDoubleClick={() => canOpen && onOpen(viz.id)}
      onKeyDown={handleKey}
      className={cn(
        'group relative flex cursor-pointer flex-col justify-center gap-1 overflow-hidden rounded-lg border bg-card py-2 pr-3 pl-4 text-left shadow-sm transition-[opacity,box-shadow,border-color] outline-none',
        'hover:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring/60',
        selected ? 'border-primary ring-2 ring-primary/40' : 'border-border',
        viz.type === 'neighbor' && 'border-dashed bg-card/70',
        viz.inCycle && !selected && 'border-destructive/60',
        dimmed && 'opacity-35',
      )}
      style={{ width: SIZE[viz.type].width, height: SIZE[viz.type].height }}
    >
      <Handle type="target" position={Position.Left} className="!size-1.5 !border-0 !bg-transparent" isConnectable={false} />
      <Handle type="source" position={Position.Right} className="!size-1.5 !border-0 !bg-transparent" isConnectable={false} />
      <span className={cn('absolute inset-y-0 left-0 w-1', TONE_CLASSES[tone].bar)} aria-hidden="true" />

      {farOut ? (
        <>
          <span className="truncate text-[22px] leading-tight font-semibold">{name}</span>
          {viz.type === 'module' && (
            <span className="truncate text-[15px] leading-none text-muted-foreground">
              {count(viz.fileCount, 'file')}
            </span>
          )}
        </>
      ) : (
        <>
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-sm font-medium">{name}</span>
            {viz.inCycle && (
              <span className="shrink-0 text-destructive" title="Part of a circular import">
                <RefreshCcw className="size-3.5" aria-hidden="true" />
              </span>
            )}
          </div>
          {parent && <span className="-mt-0.5 truncate font-mono text-[11px] text-muted-foreground">{parent}</span>}

          {viz.type === 'neighbor' ? (
            <span className="text-[11px] text-muted-foreground">
              {viz.moduleId ? 'Other module' : 'Other directory'}
              {viz.moduleId && <span className="ml-1 text-muted-foreground/70">(double-click to open)</span>}
            </span>
          ) : (
            <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
              <KindBadge kind={viz.kind} />
              {viz.type === 'module' && (
                <span>{count(viz.fileCount, 'file')}</span>
              )}
            </div>
          )}
          {viz.type !== 'neighbor' && (
            <div className="flex items-center gap-3 text-[11px] text-muted-foreground tabular-nums">
              <span className="inline-flex items-center gap-0.5" title="Depends on">
                <ArrowRight className="size-3" aria-hidden="true" />
                {viz.outgoing}
              </span>
              <span className="inline-flex items-center gap-0.5" title="Used by">
                <ArrowLeft className="size-3" aria-hidden="true" />
                {viz.incoming}
              </span>
              {viz.unresolved > 0 && (
                <span className="inline-flex items-center gap-0.5 text-warning" title={`${viz.unresolved} unresolved imports`}>
                  <TriangleAlert className="size-3" aria-hidden="true" />
                  {viz.unresolved}
                </span>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}

const nodeTypes = { card: Card }

/** Most edges whose labels are drawn without a selection; beyond it labels would crowd the picture. */
const LABEL_LIMIT = 40

interface CanvasProps {
  graph: VizGraph
  /** Changes when the picture is a different graph, so the camera fits it again. */
  graphKey: string
  selectedNodeId: string | null
  selectedEdgeId: string | null
  onSelectNode: (id: string | null) => void
  onSelectEdge: (id: string | null) => void
  onOpenNode: (id: string) => void
  /** Nodes that match the search box; when non-empty, the others recede. */
  matchIds?: ReadonlySet<string>
}

export function GraphCanvas(props: CanvasProps) {
  return <CanvasInner {...props} />
}

const NO_MATCHES: ReadonlySet<string> = new Set()

function CanvasInner({
  graph, graphKey, selectedNodeId, selectedEdgeId, onSelectNode, onSelectEdge, onOpenNode, matchIds = NO_MATCHES,
}: CanvasProps) {
  const { resolvedTheme } = useTheme()
  // On a phone, fitting a wide graph makes it unreadable: start at a readable zoom instead ("Fit to view" still shows all).
  const [narrow] = useState(() => typeof window !== 'undefined' && window.innerWidth < 640)

  const layout = useMemo(
    () =>
      layoutGraph(
        graph.nodes.map((n) => ({ id: n.id, ...SIZE[n.type] })),
        graph.edges.map((e) => ({ source: e.source, target: e.target })),
      ),
    [graph],
  )

  const focus = useMemo(() => {
    if (selectedNodeId) return neighbourhood(graph, selectedNodeId)
    if (selectedEdgeId) {
      const edge = graph.edges.find((e) => e.id === selectedEdgeId)
      if (edge) return { nodes: new Set([edge.source, edge.target]), edges: new Set([edge.id]) }
    }
    return null
  }, [graph, selectedNodeId, selectedEdgeId])

  const nodes = useMemo<CardNode[]>(
    () =>
      graph.nodes.map((viz) => ({
        id: viz.id,
        type: 'card',
        position: layout.positions.get(viz.id) ?? { x: 0, y: 0 },
        data: {
          viz,
          dimmed: (focus !== null && !focus.nodes.has(viz.id)) || (matchIds.size > 0 && !matchIds.has(viz.id)),
          onSelect: onSelectNode,
          onOpen: onOpenNode,
        },
        selected: viz.id === selectedNodeId,
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        draggable: false,
        focusable: false,
        width: SIZE[viz.type].width,
        height: SIZE[viz.type].height,
      })),
    [graph, layout, focus, matchIds, selectedNodeId, onSelectNode, onOpenNode],
  )

  const edges = useMemo<Edge[]>(() => {
    const showAllLabels = graph.edges.length <= LABEL_LIMIT
    return graph.edges.map((e) => edgeFor(e, { focus, selected: e.id === selectedEdgeId, showAllLabels }))
  }, [graph, focus, selectedEdgeId])

  return (
    <ReactFlow
      key={graphKey}
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      colorMode={resolvedTheme === 'dark' ? 'dark' : 'light'}
      fitView
      fitViewOptions={{ padding: 0.18, maxZoom: 1.05, minZoom: narrow ? 0.5 : undefined }}
      onInit={(flow) => {
        if (!narrow) return
        // start at the left edge (the modules nothing depends on), vertically centred, instead of the middle
        const zoom = 0.55
        const height = document.querySelector('.react-flow')?.clientHeight ?? 500
        void flow.setViewport({ x: 16, y: Math.max(56, (height - layout.height * zoom) / 2), zoom })
      }}
      minZoom={0.08}
      maxZoom={1.8}
      nodesDraggable={false}
      nodesConnectable={false}
      nodesFocusable={false}
      edgesFocusable={false}
      elementsSelectable={false}
      zoomOnDoubleClick={false}
      onEdgeClick={(_, edge) => onSelectEdge(edge.id)}
      onPaneClick={() => {
        onSelectNode(null)
        onSelectEdge(null)
      }}
      aria-label="Dependency graph"
      proOptions={{ hideAttribution: false }}
    >
      <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} />
      <Controls showInteractive={false} position="bottom-right" fitViewOptions={{ padding: 0.18, maxZoom: 1.05 }} />
      {graph.nodes.length > 24 && (
        <MiniMap
          pannable
          zoomable
          position="bottom-right"
          style={{ width: 150, height: 100, marginBottom: 118 }}
          className="!hidden !bg-card sm:!block"
          maskColor="oklch(0.5 0 0 / 12%)"
          nodeColor={(n) => `var(--${toneVar(kindInfo((n.data as NodeData).viz.kind).tone)})`}
        />
      )}
      <Panel position="top-left">
        <FitButton />
      </Panel>
      <Panel position="bottom-left" className="hidden sm:block">
        <Legend />
      </Panel>
    </ReactFlow>
  )
}

function toneVar(tone: string): string {
  return { api: 'chart-2', frontend: 'chart-1', database: 'chart-3', workers: 'chart-4', serverless: 'chart-5', entry: 'primary' }[tone] ?? 'border'
}

function FitButton() {
  const { fitView } = useReactFlow()
  return (
    <Button size="xs" variant="outline" className="bg-card/90 backdrop-blur" onClick={() => fitView({ padding: 0.18, maxZoom: 1.05, duration: 250 })}>
      <Maximize aria-hidden="true" />
      Fit to view
    </Button>
  )
}

function Legend() {
  return (
    <ul className="flex flex-col gap-1 rounded-md border border-border bg-card/90 px-2.5 py-2 text-[11px] text-muted-foreground backdrop-blur" aria-label="Legend">
      <li className="flex items-center gap-2">
        <span className="h-px w-5 bg-muted-foreground" aria-hidden="true" /> Depends on (arrow points at the dependency)
      </li>
      <li className="flex items-center gap-2">
        <span className="w-5 border-t border-dashed border-muted-foreground" aria-hidden="true" /> Includes a heuristic resolution
      </li>
      <li className="flex items-center gap-2">
        <span className="h-px w-5 bg-destructive" aria-hidden="true" /> Circular import
      </li>
    </ul>
  )
}

function edgeFor(
  e: VizEdge,
  { focus, selected, showAllLabels }: { focus: { edges: Set<string> } | null; selected: boolean; showAllLabels: boolean },
): Edge {
  const emphasized = selected || (focus?.edges.has(e.id) ?? false)
  const dimmed = focus !== null && !emphasized
  const heuristic = e.edges.some((b) => b.confidence !== 'verified')
  // links from a file to another module are numerous; keep them faint until something is selected
  const faint = e.id.startsWith('link:')
  const color = selected ? 'var(--primary)' : e.inCycle ? 'var(--destructive)' : emphasized ? 'var(--foreground)' : 'var(--muted-foreground)'
  const width = Math.min(4, 1 + Math.log2(e.count + 1) * 0.55) + (emphasized ? 0.6 : 0)
  return {
    id: e.id,
    source: e.source,
    target: e.target,
    type: 'default',
    label: (showAllLabels && !faint) || emphasized ? String(e.count) : undefined,
    labelStyle: { fontSize: 11, fill: 'var(--foreground)', fontWeight: 500 },
    labelBgStyle: { fill: 'var(--card)', fillOpacity: 0.95 },
    labelBgPadding: [5, 2],
    labelBgBorderRadius: 6,
    markerEnd: { type: MarkerType.ArrowClosed, color, width: 16, height: 16 },
    style: {
      stroke: color,
      strokeWidth: width,
      strokeDasharray: heuristic ? '6 4' : undefined,
      opacity: dimmed ? 0.08 : emphasized ? 1 : faint ? 0.22 : 0.6,
    },
    interactionWidth: 22,
    zIndex: emphasized ? 10 : 0,
    ariaLabel: `${e.count} ${e.count === 1 ? 'dependency' : 'dependencies'}`,
  }
}
