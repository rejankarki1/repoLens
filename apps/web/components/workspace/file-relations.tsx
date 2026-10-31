'use client'

import Link from 'next/link'
import { useMemo, useState } from 'react'
import { ArrowDownToLine, ArrowUpFromLine, ChevronRight, Network, TriangleAlert } from 'lucide-react'
import { useArchitectureGraph, useArchitectureNode, useArchitectureStatus } from '@/lib/api/hooks'
import { codeLink, fileLink, isAnalyzedPath, moduleOfPath, RELATIONSHIP_LABEL } from '@/lib/architecture/model'
import type { GraphEdge } from '@/lib/api/architecture'
import type { Repository } from '@/lib/types'
import { cn } from '@/lib/utils'

const path = (id: string) => id.replace(/^file:/, '')

/**
 * What the architecture analysis knows about the open file: the files it imports and the files that import it, each with
 * a link to the file and to the exact import statement. It only reads an existing analysis (it never starts one) and
 * renders nothing for files the analysis does not cover (docs, config) or when no analysis exists yet.
 */
export function FileRelations({ repo, file }: { repo: Repository; file: string }) {
  const status = useArchitectureStatus(repo)
  const graph = useArchitectureGraph(repo, status.data)
  const node = useArchitectureNode(repo, isAnalyzedPath(file) ? `file:${file}` : null, status.data)
  const [open, setOpen] = useState(true)
  const owner = useMemo(() => (graph.data ? moduleOfPath(graph.data.graph).get(file) : undefined), [graph.data, file])

  const detail = node.data
  if (!status.data?.has_result || !detail || node.error) return null

  const imports = [...detail.internal_edges, ...detail.outgoing].filter((e) => e.source === `file:${file}`)
  const importedBy = [...detail.internal_edges, ...detail.incoming].filter((e) => e.target === `file:${file}`)
  const stale = status.data.freshness === 'stale'
  const unresolved = detail.unresolved_imports.filter((u) => u.file === file)
  const graphHref = owner ? `/repos/${repo.id}/graph?module=${encodeURIComponent(owner.id)}&file=${encodeURIComponent(file)}` : `/repos/${repo.id}/graph`

  return (
    <section aria-label="File dependencies" className="shrink-0 border-t border-border bg-muted/30 text-xs">
      <div className="flex items-center gap-2 px-4 py-2">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="flex items-center gap-1.5 rounded font-medium hover:text-primary focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
        >
          <ChevronRight className={cn('size-3.5 transition-transform', open && 'rotate-90')} aria-hidden="true" />
          Dependencies
        </button>
        <span className="text-muted-foreground">
          {imports.length} imported · {importedBy.length} importing this file
        </span>
        <Link href={graphHref} className="ml-auto inline-flex items-center gap-1 text-primary hover:underline">
          <Network className="size-3.5" aria-hidden="true" />
          Show in architecture
        </Link>
      </div>
      {open && (
        <div className="grid max-h-56 max-sm:max-h-36 gap-x-6 gap-y-3 overflow-y-auto px-4 pb-3 sm:grid-cols-2">
          {stale && (
            <p className="flex items-start gap-1.5 text-warning sm:col-span-2">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              From an earlier analysis (commit {status.data.analyzed_commit_sha?.slice(0, 7)}); the repository has changed since.
            </p>
          )}
          <Group repoId={repo.id} title="Imports" icon={ArrowUpFromLine} edges={imports} other={(e) => path(e.target)} />
          <Group repoId={repo.id} title="Imported by" icon={ArrowDownToLine} edges={importedBy} other={(e) => path(e.source)} />
          {unresolved.length > 0 && (
            <ul className="flex flex-col gap-1 text-warning sm:col-span-2" aria-label="Unresolved imports">
              {unresolved.map((u, i) => (
                <li key={`${u.specifier}:${i}`}>
                  <Link href={codeLink(repo.id, u.evidence)} className="hover:underline">
                    Line {u.evidence.start_line}: <span className="font-mono">{u.specifier}</span> does not resolve to a file
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  )
}

function Group({
  repoId, title, icon: Icon, edges, other,
}: {
  repoId: string
  title: string
  icon: typeof ArrowUpFromLine
  edges: GraphEdge[]
  other: (e: GraphEdge) => string
}) {
  return (
    <div className="min-w-0">
      <h3 className="mb-1 flex items-center gap-1.5 font-medium text-muted-foreground">
        <Icon className="size-3.5" aria-hidden="true" />
        {title} ({edges.length})
      </h3>
      {edges.length === 0 ? (
        <p className="text-muted-foreground">None in this repository.</p>
      ) : (
        <ul className="flex flex-col gap-0.5">
          {edges.map((e) => (
            <li key={e.id} className="flex items-center justify-between gap-2">
              <Link href={fileLink(repoId, other(e))} className="truncate font-mono hover:text-primary hover:underline" title={other(e)}>
                {other(e)}
              </Link>
              {e.evidence[0] && (
                <Link
                  href={codeLink(repoId, e.evidence[0])}
                  className="shrink-0 text-muted-foreground hover:text-primary hover:underline"
                  title={`${e.evidence[0].path}: ${e.evidence[0].detail}`}
                >
                  {RELATIONSHIP_LABEL[e.relationship] ?? e.relationship} · line {e.evidence[0].start_line}
                </Link>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
