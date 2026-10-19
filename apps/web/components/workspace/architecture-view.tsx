'use client'

import Link from 'next/link'
import { useMemo, useState } from 'react'
import { ArrowDownToLine, ArrowUpFromLine, Code2, Info, MousePointerClick } from 'lucide-react'
import { Skeleton } from '@/components/ui/skeleton'
import { useDependencyGraph } from '@/lib/api/hooks'
import type { GraphNodeData, ModuleGroup, Repository } from '@/lib/types'
import { cn } from '@/lib/utils'

const COLUMNS: { title: string; groups: ModuleGroup[] }[] = [
  { title: 'Entry', groups: ['entry', 'ui'] },
  { title: 'Routing & middleware', groups: ['routing', 'middleware'] },
  { title: 'Core', groups: ['core'] },
  { title: 'Services, data & utilities', groups: ['service', 'data', 'util'] },
]

export function ArchitectureView({ repo }: { repo: Repository }) {
  const { data: graph, isLoading } = useDependencyGraph(repo)
  const [selectedPath, setSelectedPath] = useState<string | null>(null)

  const byPath = useMemo(() => new Map((graph?.nodes ?? []).map((n) => [n.path, n])), [graph])
  const selected = selectedPath ? byPath.get(selectedPath) ?? null : null

  if (isLoading) {
    return (
      <div className="grid gap-4 p-6 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-16" />
        ))}
      </div>
    )
  }

  if (!graph || graph.nodes.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 px-6 py-20 text-center">
        <div className="flex size-10 items-center justify-center rounded-lg bg-muted">
          <Info className="size-5 text-muted-foreground" aria-hidden="true" />
        </div>
        <p className="text-sm font-medium">No dependency data in demo mode</p>
        <p className="max-w-md text-sm text-muted-foreground">
          This repository only has a placeholder index. Once the backend resolves imports, module dependencies will appear here.
        </p>
      </div>
    )
  }

  const imports = new Set(selected?.imports ?? [])
  const importedBy = new Set(selected?.importedBy ?? [])

  return (
    <div className="flex h-full min-h-0 flex-col lg:flex-row">
      <div className="min-h-0 flex-1 overflow-auto p-4 sm:p-6">
        <p className="mb-4 flex items-center gap-1.5 text-xs text-muted-foreground">
          <Info className="size-3.5 shrink-0" aria-hidden="true" />
          Simplified module map from mock data. Select a module to see what it imports and what imports it.
        </p>
        <div className="grid min-w-[44rem] grid-cols-4 gap-4">
          {COLUMNS.map((col) => {
            const nodes = graph.nodes.filter((n) => col.groups.includes(n.group))
            return (
              <section key={col.title} className="flex flex-col gap-2" aria-label={col.title}>
                <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{col.title}</h2>
                {nodes.map((n) => (
                  <NodeCard
                    key={n.path}
                    node={n}
                    selected={n.path === selectedPath}
                    relation={imports.has(n.path) ? 'imports' : importedBy.has(n.path) ? 'importedBy' : null}
                    dimmed={!!selected && n.path !== selectedPath && !imports.has(n.path) && !importedBy.has(n.path)}
                    onSelect={() => setSelectedPath(n.path === selectedPath ? null : n.path)}
                  />
                ))}
              </section>
            )
          })}
        </div>
      </div>

      <aside className="shrink-0 border-t border-border bg-sidebar/50 lg:w-80 lg:border-t-0 lg:border-l" aria-label="Module details">
        {selected ? (
          <div className="flex flex-col gap-4 p-4">
            <div>
              <p className="text-sm font-semibold">{selected.label}</p>
              <p className="font-mono text-xs break-all text-muted-foreground">{selected.path}</p>
            </div>
            {selected.summary && <p className="text-sm leading-relaxed text-muted-foreground">{selected.summary}</p>}
            <Relation title="Imports" icon={ArrowUpFromLine} paths={selected.imports} onSelect={setSelectedPath} />
            <Relation title="Imported by" icon={ArrowDownToLine} paths={selected.importedBy} onSelect={setSelectedPath} />
            <Link
              href={`/repos/${repo.id}?tab=code&file=${encodeURIComponent(selected.path)}`}
              className="inline-flex h-8 items-center justify-center gap-1.5 rounded-lg border border-border text-sm hover:bg-muted"
            >
              <Code2 className="size-4" aria-hidden="true" />
              View source
            </Link>
          </div>
        ) : (
          <p className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
            <MousePointerClick className="size-4 shrink-0" aria-hidden="true" />
            Select a module to inspect it.
          </p>
        )}
      </aside>
    </div>
  )
}

function NodeCard({
  node,
  selected,
  relation,
  dimmed,
  onSelect,
}: {
  node: GraphNodeData
  selected: boolean
  relation: 'imports' | 'importedBy' | null
  dimmed: boolean
  onSelect: () => void
}) {
  return (
    <button
      onClick={onSelect}
      aria-pressed={selected}
      className={cn(
        'flex flex-col gap-0.5 rounded-lg border bg-card px-3 py-2.5 text-left transition-colors hover:bg-accent/40',
        selected ? 'border-primary ring-1 ring-primary' : 'border-border',
        relation === 'imports' && 'border-primary/50 bg-primary/5',
        relation === 'importedBy' && 'border-warning/60 bg-warning/5',
        dimmed && 'opacity-45',
      )}
    >
      <span className="truncate text-sm font-medium">{node.label}</span>
      <span className="truncate font-mono text-[11px] text-muted-foreground">{node.path}</span>
      {relation && (
        <span className="mt-1 text-[11px] text-muted-foreground">{relation === 'imports' ? 'Imported by selection' : 'Imports selection'}</span>
      )}
    </button>
  )
}

function Relation({
  title,
  icon: Icon,
  paths,
  onSelect,
}: {
  title: string
  icon: typeof Code2
  paths: string[]
  onSelect: (path: string) => void
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <h3 className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <Icon className="size-3.5" aria-hidden="true" />
        {title} ({paths.length})
      </h3>
      {paths.length === 0 ? (
        <p className="text-xs text-muted-foreground">None</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {paths.map((p) => (
            <li key={p}>
              <button onClick={() => onSelect(p)} className="w-full truncate rounded px-1.5 py-1 text-left font-mono text-xs hover:bg-accent">
                {p}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
