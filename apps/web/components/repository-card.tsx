'use client'

import Link from 'next/link'
import { useState } from 'react'
import { Code2, GitBranch, GitCommitHorizontal, History, Loader2, MessageSquareText, Network, RefreshCw, TriangleAlert } from 'lucide-react'
import { IndexBadge } from '@/components/index-badge'
import { IndexingProgressBar, IndexingStatus, STATUS_HELP } from '@/components/indexing-status'
import { Button } from '@/components/ui/button'
import { describeIngestionError, startIngestion } from '@/lib/api/client'
import { useSearchIndex } from '@/lib/api/hooks'
import { formatRelativeTime, isIngesting } from '@/lib/repo-utils'
import type { Repository } from '@/lib/types'

const ACTION =
  'inline-flex h-7 items-center gap-1.5 rounded-md border border-border bg-background px-2 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/40'

/** The way forward for a repository that has no usable snapshot: try again, or start the first ingestion. */
function RecoveryAction({ repository }: { repository: Repository }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const failed = repository.status === 'failed'
  async function run() {
    setBusy(true)
    setError(null)
    try {
      await startIngestion(repository.id)
    } catch (err) {
      const { message, alreadyActive } = describeIngestionError(err)
      if (!alreadyActive) setError(message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="relative z-10 flex flex-col items-start gap-1.5">
      <Button size="sm" variant="outline" onClick={run} disabled={busy}>
        {busy ? <Loader2 className="animate-spin" aria-hidden="true" /> : <RefreshCw aria-hidden="true" />}
        {failed ? 'Try again' : 'Start ingestion'}
      </Button>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}

/** A short, muted "opened 3h ago" note; absent when the repository has never been opened in this browser. */
function recentlyOpenedLabel(openedAt: number | undefined): string | null {
  if (!openedAt) return null
  return formatRelativeTime(new Date(openedAt).toISOString())
}

/**
 * A dashboard card built only from what the API knows: ingestion status, the semantic-index state (for your own
 * repositories, without polling), and when you last opened it here. The whole card opens the repository; the
 * three actions go straight to a tab. Status and search-readiness lead; branch and commit are a quiet detail line.
 */
export function RepositoryCard({ repository, openedAt }: { repository: Repository; openedAt?: number }) {
  const { id, owner, name, status, progress, lastAnalyzedAt, error, fullName, defaultBranch, indexedCommit, truncated, viewerAccess } = repository
  const hasSnapshot = indexedCommit !== null
  // The index status is member-only on the server; visitors browsing the demo would only get a refusal.
  const { data: index } = useSearchIndex(viewerAccess === 'member' && status === 'completed' ? repository : undefined, { poll: false })
  const openedLabel = recentlyOpenedLabel(openedAt)

  return (
    <li className="relative flex min-w-0 flex-col gap-2.5 rounded-lg border border-border bg-card px-4 py-3 transition focus-within:border-primary/40 hover:border-primary/40 hover:bg-accent/30 focus-within:bg-accent/30">
      <div className="flex items-center justify-between gap-3">
        <Link
          href={`/repos/${id}`}
          title={fullName}
          className="min-w-0 rounded text-sm after:absolute after:inset-0 after:rounded-lg focus-visible:outline-none focus-visible:after:ring-3 focus-visible:after:ring-ring/40"
        >
          <span className="block truncate">
            <span className="text-muted-foreground">{owner}/</span>
            <span className="font-semibold">{name}</span>
          </span>
        </Link>
        <span className="flex shrink-0 items-center gap-2">
          {openedLabel && (
            <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground" title={`You opened this ${openedLabel}`}>
              <History className="size-3" aria-hidden="true" />
              {openedLabel}
            </span>
          )}
          <IndexingStatus status={status} progress={progress} />
        </span>
      </div>

      <IndexingProgressBar status={status} progress={progress} />

      {status === 'failed' && error ? (
        <p className="line-clamp-2 text-xs text-destructive/80" title={error}>
          {error}
        </p>
      ) : isIngesting(status) ? (
        <p className="text-xs text-muted-foreground">{STATUS_HELP[status]}</p>
      ) : (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-muted-foreground">
          <IndexBadge index={index} />
          <span>{lastAnalyzedAt ? `Ingested ${formatRelativeTime(lastAnalyzedAt)}` : 'Not ingested yet'}</span>
          {truncated && (
            <span className="inline-flex items-center gap-1 text-warning" title="The repository exceeded the ingestion limits, so only part of it is stored.">
              <TriangleAlert className="size-3.5" aria-hidden="true" />
              Partial snapshot
            </span>
          )}
          <span className="ml-auto inline-flex items-center gap-2.5 text-muted-foreground/60">
            <span className="inline-flex items-center gap-1">
              <GitBranch className="size-3.5" aria-hidden="true" />
              <span className="font-mono">{defaultBranch}</span>
            </span>
            {indexedCommit && (
              <span className="inline-flex items-center gap-1" title="Ingested commit">
                <GitCommitHorizontal className="size-3.5" aria-hidden="true" />
                <span className="font-mono">{indexedCommit}</span>
              </span>
            )}
          </span>
        </div>
      )}

      {!hasSnapshot && (status === 'failed' || status === 'pending') && viewerAccess === 'member' && <RecoveryAction repository={repository} />}

      {hasSnapshot && (
        <div className="relative z-10 flex flex-wrap gap-2">
          <Link href={`/repos/${id}?tab=ask`} className={ACTION}>
            <MessageSquareText className="size-3.5" aria-hidden="true" />
            Ask AI
          </Link>
          <Link href={`/repos/${id}?tab=code`} className={ACTION}>
            <Code2 className="size-3.5" aria-hidden="true" />
            Explore Code
          </Link>
          <Link href={`/repos/${id}/graph`} className={ACTION}>
            <Network className="size-3.5" aria-hidden="true" />
            Architecture
          </Link>
        </div>
      )}
    </li>
  )
}
