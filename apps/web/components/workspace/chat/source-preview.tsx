'use client'

import Link from 'next/link'
import { useEffect } from 'react'
import { Code2, FileCode2, TriangleAlert, X } from 'lucide-react'
import { CodeViewer } from '@/components/workspace/code-viewer'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import type { SourceCitation } from '@/lib/api/chat'
import { useFile } from '@/lib/api/hooks'
import { citationHref, formatRange, isStaleCitation, rangeProblem } from '@/lib/chat-citations'
import type { Repository } from '@/lib/types'

/**
 * The cited file with the cited lines highlighted, shown next to the conversation (wide screens) or in a drawer.
 * It shows the repository's *current* snapshot of the file, so it says so when the answer was written from another
 * commit or when the cited lines no longer fit the file; the full Code Explorer stays one click away.
 */
export function SourcePreview({
  repo, citation, onClose, closeOnEscape,
}: {
  repo: Repository
  citation: SourceCitation
  onClose: () => void
  /** Side-by-side mode is not modal, so it handles Escape itself; the drawer already does. */
  closeOnEscape: boolean
}) {
  const { data, error, isLoading } = useFile(repo, citation.path)
  // `useFile` keeps the previous file while the next one loads; never show it under another citation's highlight.
  const source = data?.path === citation.path ? data : undefined
  const stale = isStaleCitation(citation, repo.indexedCommit)
  const totalLines = source ? source.content.replace(/\n$/, '').split('\n').length : 0
  const problem = source ? rangeProblem(citation, totalLines) : null
  const name = citation.path.split('/').pop() ?? citation.path
  const range = formatRange(citation.startLine, citation.endLine)

  useEffect(() => {
    if (!closeOnEscape) return
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (e.key === 'Escape' && !target?.closest('input, textarea, [contenteditable=true]')) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [closeOnEscape, onClose])

  return (
    <section className="flex h-full min-h-0 flex-col bg-card" aria-label={`Source preview: ${citation.path}`}>
      <header className="flex items-start gap-2 border-b border-border px-3 py-2.5">
        <FileCode2 className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-x-2 text-sm font-medium">
            <span className="truncate">{name}</span>
            <span className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[11px] font-normal text-primary">
              {citation.id} · lines {range}
            </span>
          </p>
          {(citation.path !== name || citation.symbolName) && (
            <p className="truncate font-mono text-[11px] text-muted-foreground" title={citation.path}>
              {citation.path !== name ? citation.path : ''}
              {citation.symbolName ? `${citation.path !== name ? ' · ' : ''}${citation.symbolName}` : ''}
            </p>
          )}
        </div>
        <Button size="icon-sm" variant="ghost" onClick={onClose} aria-label="Close source preview">
          <X aria-hidden="true" />
        </Button>
      </header>

      {stale && (
        <p role="note" className="flex items-start gap-2 border-b border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning">
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          This answer was written from commit {citation.commitSha.slice(0, 7)}, but the repository is now at {repo.indexedCommit}. The
          file below is the current version, so the highlighted lines may have moved.
        </p>
      )}
      {problem && (
        <p role="note" className="flex items-start gap-2 border-b border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning">
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          {problem}
        </p>
      )}

      {source ? (
        <CodeViewer
          key={`${source.path}:${citation.startLine}-${citation.endLine}`}
          content={source.content.replace(/\n$/, '')}
          language={source.language}
          highlight={problem ? null : { start: citation.startLine, end: citation.endLine }}
          className="min-h-0 flex-1"
        />
      ) : error ? (
        <p role="alert" className="p-4 text-sm text-destructive">
          Could not load this file. It may not exist in the repository&apos;s current snapshot. Try the Code Explorer link below.
        </p>
      ) : (
        <div className="flex flex-1 flex-col gap-2 p-4" aria-busy={isLoading} aria-label="Loading source">
          {Array.from({ length: 10 }).map((_, i) => (
            <Skeleton key={i} className="h-4" style={{ width: `${35 + ((i * 37) % 55)}%` }} />
          ))}
        </div>
      )}

      <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-3 py-2">
        <Link
          href={citationHref(repo.id, citation)}
          className="inline-flex h-7 items-center gap-1.5 rounded-lg border border-border px-2.5 text-[0.8rem] hover:bg-muted"
        >
          <Code2 className="size-3.5" aria-hidden="true" />
          Open in Code Explorer
        </Link>
        <span className="text-[11px] text-muted-foreground">Commit {citation.commitSha.slice(0, 7)}</span>
      </footer>
    </section>
  )
}
