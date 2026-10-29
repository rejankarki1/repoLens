'use client'

import Link from 'next/link'
import { useMemo, useState, type ReactNode } from 'react'
import { Code2, FileCode2, FolderTree, Layers, RefreshCcw, TriangleAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  ClassificationEvidence,
  ConfidenceNote,
  EdgeEvidence,
  EvidenceItem,
  KindBadge,
  ProvenanceBadge,
  Section,
  Stat,
  TONE_CLASSES,
} from '@/components/workspace/architecture/evidence'
import type { Area, Cycle, Evidence, FileExternal, Graph, GraphEdge, GraphNode, NodeDetail, Technology, UnresolvedImport } from '@/lib/api/architecture'
import {
  fileLink, isInstallable, kindInfo, moduleRelations, RELATIONSHIP_LABEL, UNRESOLVED_REASON, type VizEdge, type VizGraph, type VizNode,
} from '@/lib/architecture/model'
import { cn } from '@/lib/utils'

type Labeler = (id: string) => string

const PANEL = 'flex flex-col gap-5 p-4'

function PanelHeader({ title, path, children }: { title: string; path?: string; children?: ReactNode }) {
  return (
    <header className="flex flex-col gap-2">
      <div>
        <h2 className="text-base font-semibold break-words">{title}</h2>
        {path && path !== title && <p className="font-mono text-xs break-all text-muted-foreground">{path}</p>}
      </div>
      {children}
    </header>
  )
}

/* ============================== Overview insights ============================== */

export function OverviewInsights({
  repoId, graph, onSelectModule,
}: {
  repoId: string
  graph: Graph
  onSelectModule: (id: string) => void
}) {
  const { stats } = graph
  const labelOf = (id: string) => graph.nodes.find((n) => n.id === id)?.label ?? id
  const withUnresolved = graph.nodes.filter((n) => n.metrics.unresolved_imports > 0)

  return (
    <div className={PANEL}>
      <PanelHeader title="Architecture overview">
        <p className="text-xs leading-relaxed text-muted-foreground">
          Arrows point from a module to the modules it imports. Select a module for its evidence, or an arrow for the import statements
          behind it.
        </p>
      </PanelHeader>

      <div className="grid grid-cols-2 gap-2" aria-label="Summary">
        <Stat label="Modules" value={stats.modules} />
        <Stat label="Code files" value={stats.code_files} />
        <Stat label="File dependencies" value={stats.file_edges} />
        <Stat label="External packages" value={stats.external_packages} />
        <Stat label="Circular imports" value={stats.cycles} tone={stats.cycles > 0 ? 'danger' : undefined} />
        <Stat label="Unresolved imports" value={stats.unresolved_imports} tone={stats.unresolved_imports > 0 ? 'warning' : undefined} />
      </div>

      {graph.warnings.length > 0 && (
        <ul className="flex flex-col gap-1.5 rounded-md border border-warning/40 bg-warning/10 p-2.5 text-xs text-warning" aria-label="Analysis warnings">
          {graph.warnings.map((w) => (
            <li key={w} className="flex items-start gap-1.5">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              {w}
            </li>
          ))}
        </ul>
      )}

      <Technologies repoId={repoId} items={graph.technologies} />
      <Areas repoId={repoId} areas={graph.areas} labelOf={labelOf} onSelectModule={onSelectModule} />
      <CycleList cycles={graph.cycles.filter((c) => c.level === 'module')} total={stats.cycles} labelOf={labelOf} onSelectModule={onSelectModule} />
      {withUnresolved.length > 0 && (
        <Section title="Unresolved imports" count={stats.unresolved_imports}>
          <p className="text-xs text-muted-foreground">
            Imports that name a file or alias that does not exist in this snapshot. Open a module to see the statements.
          </p>
          <ul className="flex flex-col gap-1">
            {withUnresolved.map((m) => (
              <li key={m.id}>
                <button
                  type="button"
                  onClick={() => onSelectModule(m.id)}
                  className="flex w-full items-center justify-between gap-2 rounded-md px-2 py-1 text-left text-xs hover:bg-accent"
                >
                  <span className="truncate font-mono">{labelOf(m.id)}</span>
                  <span className="shrink-0 text-warning tabular-nums">{m.metrics.unresolved_imports}</span>
                </button>
              </li>
            ))}
          </ul>
        </Section>
      )}
      <Externals repoId={repoId} items={graph.external_dependencies} total={stats.external_packages} />
    </div>
  )
}

function Technologies({ repoId, items }: { repoId: string; items: Technology[] }) {
  const [open, setOpen] = useState<string | null>(null)
  if (items.length === 0) return null
  const current = items.find((t) => t.id === open)
  return (
    <Section title="Technologies" count={items.length}>
      <p className="text-xs text-muted-foreground">Detected from declared dependencies and imports, never from folder names.</p>
      <ul className="flex flex-wrap gap-1.5">
        {items.map((t) => (
          <li key={t.id}>
            <button
              type="button"
              aria-pressed={open === t.id}
              onClick={() => setOpen(open === t.id ? null : t.id)}
              className={cn(
                'inline-flex h-6 items-center gap-1.5 rounded-full border px-2.5 text-xs transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none',
                open === t.id ? 'border-primary bg-primary/10' : 'border-border bg-card',
              )}
              title={t.category}
            >
              {t.name}
            </button>
          </li>
        ))}
      </ul>
      {current && <ClassificationEvidence repoId={repoId} items={current.evidence} title={`Evidence for ${current.name}`} />}
    </Section>
  )
}

function Areas({
  repoId, areas, labelOf, onSelectModule,
}: {
  repoId: string
  areas: Area[]
  labelOf: Labeler
  onSelectModule: (id: string) => void
}) {
  const [open, setOpen] = useState<string | null>(null)
  if (areas.length === 0) return null
  return (
    <Section title="Architecture areas" count={areas.length}>
      <div className="flex items-center gap-2">
        <ProvenanceBadge kind="inferred" />
        <span className="text-xs text-muted-foreground">Classified from code evidence</span>
      </div>
      <ul className="flex flex-col gap-1.5">
        {areas.map((a) => {
          const { tone } = kindInfo(a.kind)
          return (
            <li key={a.id} className="rounded-lg border border-border bg-card">
              <button
                type="button"
                aria-expanded={open === a.id}
                onClick={() => setOpen(open === a.id ? null : a.id)}
                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left hover:bg-accent/50 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
              >
                <span className={cn('size-2 shrink-0 rounded-full', TONE_CLASSES[tone].dot)} aria-hidden="true" />
                <span className="min-w-0 flex-1 truncate text-sm">{a.label}</span>
                <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                  {a.file_count > 0 ? `${a.file_count} ${a.file_count === 1 ? 'file' : 'files'}` : 'SQL only'} · {a.confidence}
                </span>
              </button>
              {open === a.id && (
                <div className="flex flex-col gap-2 border-t border-border p-2.5">
                  {a.module_ids.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {a.module_ids.map((id) => (
                        <button
                          key={id}
                          type="button"
                          onClick={() => onSelectModule(id)}
                          className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[11px] hover:bg-accent"
                        >
                          {labelOf(id)}
                        </button>
                      ))}
                    </div>
                  )}
                  <ClassificationEvidence repoId={repoId} items={a.evidence} title="Evidence" />
                </div>
              )}
            </li>
          )
        })}
      </ul>
    </Section>
  )
}

function CycleList({
  cycles, total, labelOf, onSelectModule,
}: {
  cycles: Cycle[]
  total: number
  labelOf: Labeler
  onSelectModule: (id: string) => void
}) {
  if (total === 0) return null
  const fileCycles = total - cycles.length
  return (
    <Section title="Circular imports" count={total}>
      <p className="text-xs text-muted-foreground">
        Modules or files that import each other at load time. Lazy and type-only imports are not counted.
      </p>
      <ul className="flex flex-col gap-1.5">
        {cycles.map((c) => (
          <li key={c.id} className="rounded-lg border border-destructive/40 bg-destructive/5 p-2.5 text-xs">
            <p className="mb-1.5 flex items-center gap-1.5 font-medium text-destructive">
              <RefreshCcw className="size-3.5" aria-hidden="true" />
              {c.size} modules
            </p>
            <div className="flex flex-wrap gap-1">
              {c.nodes.map((id) => (
                <button key={id} type="button" onClick={() => onSelectModule(id)} className="rounded-md bg-background px-1.5 py-0.5 font-mono text-[11px] hover:bg-accent">
                  {labelOf(id)}
                </button>
              ))}
              {c.truncated && <span className="px-1 text-muted-foreground">+{c.size - c.nodes.length} more</span>}
            </div>
          </li>
        ))}
      </ul>
      {fileCycles > 0 && (
        <p className="text-xs text-muted-foreground">
          {fileCycles} file-level {fileCycles === 1 ? 'cycle' : 'cycles'} inside modules: open a module marked <RefreshCcw className="inline size-3 text-destructive" aria-label="circular" /> to see which files.
        </p>
      )}
    </Section>
  )
}

function Externals({ repoId, items, total }: { repoId: string; items: Graph['external_dependencies']; total: number }) {
  const [all, setAll] = useState(false)
  const [builtins, setBuiltins] = useState(false)
  const shown = items.filter((d) => builtins || isInstallable(d.ecosystem))
  const visible = all ? shown : shown.slice(0, 10)
  if (total === 0) return null
  return (
    <Section title="External packages" count={shown.length} defaultOpen={false}>
      <label className="flex items-center gap-2 text-xs text-muted-foreground">
        <input type="checkbox" checked={builtins} onChange={(e) => setBuiltins(e.target.checked)} className="accent-primary" />
        Include standard library and built-ins
      </label>
      <ul className="flex flex-col gap-1">
        {visible.map((d) => (
          <li key={`${d.ecosystem}:${d.name}`} className="flex items-center justify-between gap-2 rounded-md px-2 py-1 text-xs hover:bg-accent/50">
            <span className="min-w-0 truncate font-mono" title={d.name}>
              {d.name}
            </span>
            <span className="flex shrink-0 items-center gap-2 text-muted-foreground">
              <span>{d.ecosystem.replace('_', ' ')}</span>
              {d.evidence[0] && (
                <Link href={fileLink(repoId, d.evidence[0].path)} className="tabular-nums hover:text-primary hover:underline" title={`First imported in ${d.evidence[0].path}`}>
                  {d.importer_count} {d.importer_count === 1 ? 'file' : 'files'}
                </Link>
              )}
            </span>
          </li>
        ))}
      </ul>
      {shown.length > 10 && (
        <Button type="button" size="xs" variant="ghost" className="self-start" onClick={() => setAll((a) => !a)}>
          {all ? 'Show fewer' : `Show all ${shown.length}`}
        </Button>
      )}
      {items.length < total && <p className="text-[11px] text-muted-foreground">The overview lists the {items.length} most imported packages.</p>}
    </Section>
  )
}

/* ================================ Module panels ================================ */

function relationLabel(breakdown: Record<string, number>): string {
  const parts = Object.entries(breakdown).map(([k, v]) => `${v} ${RELATIONSHIP_LABEL[k] ?? k}`)
  return parts.join(' · ')
}

function RelationList({
  title, edges, other, labelOf, onPick, emptyText,
}: {
  title: string
  edges: GraphEdge[]
  other: (e: GraphEdge) => string
  labelOf: Labeler
  onPick: (edge: GraphEdge) => void
  emptyText: string
}) {
  return (
    <Section title={title} count={edges.length}>
      {edges.length === 0 ? (
        <p className="text-xs text-muted-foreground">{emptyText}</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {edges.map((e) => (
            <li key={e.id}>
              <button
                type="button"
                onClick={() => onPick(e)}
                className="flex w-full flex-col gap-0.5 rounded-md border border-border bg-card px-2.5 py-1.5 text-left hover:border-primary/40 hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-xs" title={other(e)}>
                    {labelOf(other(e))}
                  </span>
                  <span className="shrink-0 text-xs text-muted-foreground tabular-nums">{e.count}</span>
                </span>
                <span className="flex flex-wrap items-center gap-x-2 text-[11px] text-muted-foreground">
                  {relationLabel(e.breakdown)}
                  {e.cycle_id && <span className="text-destructive">circular</span>}
                  <ConfidenceNote confidence={e.confidence} />
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}

export function ModuleSummary({
  repoId, graph, node, onPickEdge, onExplore, exploring,
}: {
  repoId: string
  graph: Graph
  node: GraphNode
  /** Called with a module-level edge; the owner decides what selecting it means in the current view. */
  onPickEdge: (edge: GraphEdge) => void
  onExplore?: () => void
  exploring?: boolean
}) {
  const relations = useMemo(() => moduleRelations(graph, node.id), [graph, node.id])
  const labelOf = useMemo(() => (id: string) => graph.nodes.find((n) => n.id === id)?.label ?? id, [graph])
  const [allFiles, setAllFiles] = useState(false)
  const files = allFiles ? node.files : node.files.slice(0, 10)
  const cycle = graph.cycles.find((c) => c.id === node.cycle_id)

  return (
    <div className={PANEL}>
      <PanelHeader title={node.label === '(root)' ? 'repository root' : node.label} path={node.path}>
        <div className="flex flex-wrap items-center gap-2">
          <KindBadge kind={node.kind} />
          <span className="text-xs text-muted-foreground">
            {node.file_count} {node.file_count === 1 ? 'file' : 'files'}
          </span>
        </div>
        {onExplore && !exploring && (
          <Button size="sm" className="self-start" onClick={onExplore}>
            <FolderTree aria-hidden="true" />
            Explore files
          </Button>
        )}
      </PanelHeader>

      {cycle && (
        <p role="note" className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-2.5 text-xs text-destructive">
          <RefreshCcw className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          Part of a circular import between {cycle.size} modules:{' '}
          {cycle.nodes.map(labelOf).join(', ')}
          {cycle.truncated ? ', …' : ''}.
        </p>
      )}

      <ClassificationEvidence repoId={repoId} items={node.evidence} title={`Why it is classed as ${kindInfo(node.kind).label ?? node.kind}`} />

      <div className="grid grid-cols-3 gap-2">
        <Stat label="Depends on" value={node.metrics.outgoing} />
        <Stat label="Used by" value={node.metrics.incoming} />
        <Stat label="Packages" value={node.metrics.external_packages} />
      </div>
      {node.metrics.unresolved_imports > 0 && (
        <p className="flex items-center gap-1.5 text-xs text-warning">
          <TriangleAlert className="size-3.5" aria-hidden="true" />
          {node.metrics.unresolved_imports} unresolved {node.metrics.unresolved_imports === 1 ? 'import' : 'imports'}
          {onExplore && !exploring ? ' — explore the files to see them' : ''}
        </p>
      )}

      <RelationList
        title="Depends on"
        edges={relations.out}
        other={(e) => e.target}
        labelOf={labelOf}
        onPick={onPickEdge}
        emptyText="This module does not import any other module."
      />
      <RelationList
        title="Used by"
        edges={relations.in}
        other={(e) => e.source}
        labelOf={labelOf}
        onPick={onPickEdge}
        emptyText="No other module imports this one."
      />

      <Section title="Files" count={node.file_count} defaultOpen={false}>
        <ul className="flex flex-col gap-0.5">
          {files.map((f) => (
            <li key={f}>
              <Link href={fileLink(repoId, f)} className="flex items-center gap-1.5 rounded px-1.5 py-0.5 font-mono text-xs hover:bg-accent hover:text-primary">
                <FileCode2 className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
                <span className="truncate">{f}</span>
              </Link>
            </li>
          ))}
        </ul>
        {node.file_count > 10 && (
          <Button type="button" size="xs" variant="ghost" className="self-start" onClick={() => setAllFiles((a) => !a)}>
            {allFiles ? 'Show fewer' : `Show all ${node.files.length}${node.files_truncated ? ` of ${node.file_count}` : ''}`}
          </Button>
        )}
      </Section>
    </div>
  )
}

export function ModuleEdgePanel({
  repoId, edges, labelOf, onExplore,
}: {
  repoId: string
  edges: GraphEdge[]
  labelOf: Labeler
  onExplore?: (moduleId: string) => void
}) {
  return (
    <div className={PANEL}>
      {edges.map((edge) => (
        <div key={edge.id} className="flex flex-col gap-3">
          <PanelHeader title={`${labelOf(edge.source)} → ${labelOf(edge.target)}`}>
            <p className="text-xs leading-relaxed text-muted-foreground">
              A module-level dependency, summed from the file imports below. The statements are the real source of every arrow.
            </p>
            {edge.cycle_id && (
              <p className="flex items-center gap-1.5 text-xs text-destructive">
                <RefreshCcw className="size-3.5" aria-hidden="true" /> Part of a circular import.
              </p>
            )}
          </PanelHeader>
          <EdgeEvidence repoId={repoId} edge={edge} labelOf={labelOf} showEndpoints={false} />
          {onExplore && (
            <div className="flex flex-wrap gap-2">
              <Button size="xs" variant="outline" onClick={() => onExplore(edge.source)}>
                Explore {labelOf(edge.source)}
              </Button>
              <Button size="xs" variant="outline" onClick={() => onExplore(edge.target)}>
                Explore {labelOf(edge.target)}
              </Button>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

/* ============================= File-level (detail) ============================= */

function groupExternals(items: FileExternal[]) {
  const byName = new Map<string, { name: string; ecosystem: string; files: Set<string>; first: Evidence }>()
  for (const x of items) {
    const entry = byName.get(`${x.ecosystem}:${x.name}`)
    if (entry) entry.files.add(x.file)
    else byName.set(`${x.ecosystem}:${x.name}`, { name: x.name, ecosystem: x.ecosystem, files: new Set([x.file]), first: x.evidence })
  }
  return [...byName.values()].sort((a, b) => b.files.size - a.files.size || a.name.localeCompare(b.name))
}

function ExternalList({ repoId, items, showAll }: { repoId: string; items: FileExternal[]; showAll?: boolean }) {
  const [builtins, setBuiltins] = useState(false)
  const grouped = groupExternals(items).filter((g) => builtins || isInstallable(g.ecosystem))
  if (items.length === 0) return null
  return (
    <Section title="External packages" count={grouped.length} defaultOpen={showAll}>
      <label className="flex items-center gap-2 text-xs text-muted-foreground">
        <input type="checkbox" checked={builtins} onChange={(e) => setBuiltins(e.target.checked)} className="accent-primary" />
        Include standard library and built-ins
      </label>
      <ul className="flex flex-col gap-1">
        {grouped.map((g) => (
          <li key={`${g.ecosystem}:${g.name}`} className="flex items-center justify-between gap-2 text-xs">
            <Link href={`/repos/${repoId}?tab=code&file=${encodeURIComponent(g.first.path)}&lines=${g.first.start_line}`} className="min-w-0 truncate font-mono hover:text-primary hover:underline" title={g.first.detail}>
              {g.name}
            </Link>
            <span className="shrink-0 text-muted-foreground tabular-nums">
              {g.files.size} {g.files.size === 1 ? 'file' : 'files'}
            </span>
          </li>
        ))}
      </ul>
    </Section>
  )
}

function UnresolvedList({ repoId, items }: { repoId: string; items: UnresolvedImport[] }) {
  if (items.length === 0) return null
  return (
    <Section title="Unresolved imports" count={items.length}>
      <p className="text-xs text-muted-foreground">No file was created for these: the import names something that is not in this snapshot.</p>
      <ul className="flex flex-col gap-1.5">
        {items.map((u, i) => (
          <li key={`${u.file}:${u.evidence.start_line}:${i}`} className="flex flex-col gap-1">
            <p className="text-[11px] text-warning">
              <span className="font-mono">{u.specifier}</span> — {UNRESOLVED_REASON[u.reason] ?? u.reason}
            </p>
            <ul>
              <EvidenceItem repoId={repoId} evidence={u.evidence} showProvenance={false} />
            </ul>
          </li>
        ))}
      </ul>
    </Section>
  )
}

function CyclesNote({ cycles }: { cycles: Cycle[] }) {
  if (cycles.length === 0) return null
  return (
    <p role="note" className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-2.5 text-xs text-destructive">
      <RefreshCcw className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
      <span>
        {cycles.map((c) => `Circular import between ${c.size} ${c.level === 'module' ? 'modules' : 'files'}`).join('; ')}. Edges in red belong to it.
      </span>
    </p>
  )
}

/** Default panel of the file-level view: the module's own summary. */
export function ModuleDetailSummary({
  repoId, graph, detail, viz, onPickEdge, onBack,
}: {
  repoId: string
  graph: Graph
  detail: NodeDetail
  viz: VizGraph
  onPickEdge: (edge: GraphEdge) => void
  onBack: () => void
}) {
  const [hiddenOpen, setHiddenOpen] = useState(false)
  return (
    <div>
      <ModuleSummary repoId={repoId} graph={graph} node={detail.node} onPickEdge={onPickEdge} exploring />
      <div className="flex flex-col gap-5 px-4 pb-4">
        <CyclesNote cycles={detail.cycles} />
        <UnresolvedList repoId={repoId} items={detail.unresolved_imports} />
        <ExternalList repoId={repoId} items={detail.external_dependencies} showAll />
        {viz.hidden.length > 0 && (
          <Section title="Files without dependencies" count={viz.hidden.length} defaultOpen={false}>
            <p className="text-xs text-muted-foreground">These files import nothing in the repository and nothing imports them, so they are not drawn.</p>
            <ul className="flex flex-col gap-0.5">
              {(hiddenOpen ? viz.hidden : viz.hidden.slice(0, 8)).map((f) => (
                <li key={f.id}>
                  <Link href={fileLink(repoId, f.path)} className="block truncate rounded px-1.5 py-0.5 font-mono text-xs hover:bg-accent hover:text-primary">
                    {f.path}
                  </Link>
                </li>
              ))}
            </ul>
            {viz.hidden.length > 8 && (
              <Button type="button" size="xs" variant="ghost" className="self-start" onClick={() => setHiddenOpen((o) => !o)}>
                {hiddenOpen ? 'Show fewer' : `Show all ${viz.hidden.length}`}
              </Button>
            )}
          </Section>
        )}
        <Button variant="outline" size="sm" className="self-start" onClick={onBack}>
          Back to module overview
        </Button>
      </div>
    </div>
  )
}

export function FilePanel({
  repoId, detail, node, labelOf,
}: {
  repoId: string
  detail: NodeDetail
  node: GraphNode
  labelOf: Labeler
}) {
  const imports = [...detail.internal_edges, ...detail.outgoing].filter((e) => e.source === node.id)
  const importedBy = [...detail.internal_edges, ...detail.incoming].filter((e) => e.target === node.id)
  const externals = detail.external_dependencies.filter((x) => x.file === node.path)
  const unresolved = detail.unresolved_imports.filter((u) => u.file === node.path)
  const cycle = detail.cycles.find((c) => c.id === node.cycle_id)
  const info = kindInfo(node.kind)

  return (
    <div className={PANEL}>
      <PanelHeader title={node.label} path={node.path}>
        <div className="flex flex-wrap items-center gap-2">
          <KindBadge kind={node.kind} />
          {node.language && <span className="text-xs text-muted-foreground">{node.language}</span>}
        </div>
        <Link
          href={fileLink(repoId, node.path)}
          className="inline-flex h-7 w-fit items-center gap-1.5 rounded-lg border border-border px-2.5 text-[0.8rem] hover:bg-muted"
        >
          <Code2 className="size-3.5" aria-hidden="true" />
          Open source
        </Link>
      </PanelHeader>
      {cycle && <CyclesNote cycles={[cycle]} />}
      <ClassificationEvidence repoId={repoId} items={node.evidence} title={`Why it is classed as ${info.label ?? node.kind}`} />

      <Section title="Imports" count={imports.length}>
        {imports.length === 0 ? (
          <p className="text-xs text-muted-foreground">Imports no other file in this repository.</p>
        ) : (
          imports.map((e) => <EdgeEvidence key={e.id} repoId={repoId} edge={e} labelOf={labelOf} perspective={node.id} />)
        )}
      </Section>
      <Section title="Imported by" count={importedBy.length} defaultOpen={importedBy.length <= 3}>
        {importedBy.length === 0 ? (
          <p className="text-xs text-muted-foreground">No other file in this repository imports it.</p>
        ) : (
          importedBy.map((e) => <EdgeEvidence key={e.id} repoId={repoId} edge={e} labelOf={labelOf} perspective={node.id} />)
        )}
      </Section>
      <UnresolvedList repoId={repoId} items={unresolved} />
      <ExternalList repoId={repoId} items={externals} showAll />
    </div>
  )
}

/** The imports behind a link between this module and another one (or a single file-to-file edge). */
export function LinkPanel({
  repoId, edges, labelOf, title, note,
}: {
  repoId: string
  edges: GraphEdge[]
  labelOf: Labeler
  title: string
  note?: string
}) {
  return (
    <div className={PANEL}>
      <PanelHeader title={title}>
        {note && <p className="text-xs leading-relaxed text-muted-foreground">{note}</p>}
      </PanelHeader>
      <div className="flex flex-col gap-2">
        {edges.map((e) => (
          <EdgeEvidence key={e.id} repoId={repoId} edge={e} labelOf={labelOf} />
        ))}
      </div>
    </div>
  )
}

/** Panel for a "neighbour" stub in the file view: every import between this module and that module. */
export function NeighbourPanel({
  repoId, node, vizEdges, labelOf, onOpen,
}: {
  repoId: string
  node: VizNode
  vizEdges: VizEdge[]
  labelOf: Labeler
  onOpen?: () => void
}) {
  const edges = vizEdges.filter((e) => e.source === node.id || e.target === node.id).flatMap((e) => e.edges)
  return (
    <div className={PANEL}>
      <PanelHeader title={node.label}>
        <p className="text-xs text-muted-foreground">
          {node.moduleId ? 'Another module' : 'Files outside the analyzed modules'} that this module imports from or is imported by.
        </p>
        {onOpen && (
          <Button size="sm" variant="outline" className="self-start" onClick={onOpen}>
            <Layers aria-hidden="true" />
            Explore {node.label}
          </Button>
        )}
      </PanelHeader>
      <Section title="Imports between them" count={edges.length}>
        {edges.map((e) => (
          <div key={e.id} className="flex flex-col gap-1.5">
            <EdgeEvidence repoId={repoId} edge={e} labelOf={labelOf} />
          </div>
        ))}
      </Section>
    </div>
  )
}
