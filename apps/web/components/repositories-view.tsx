'use client'

import { useState } from 'react'
import Link from 'next/link'
import { AlertCircle, CheckCircle2, FolderGit2, GitBranch, GitCommitHorizontal, Plus, RefreshCw, Search, Trash2, TriangleAlert, X } from 'lucide-react'
import { GitHubSignIn } from '@/components/auth/github-sign-in'
import { IndexBadge } from '@/components/index-badge'
import { IndexingStatus } from '@/components/indexing-status'
import { PageHeader, PageShell } from '@/components/page-shell'
import { RemoveRepositoryDialog } from '@/components/remove-repository-dialog'
import { SlowStartHint } from '@/components/slow-start-hint'
import { Button, buttonVariants } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { describeIngestionError, startIngestion } from '@/lib/api/client'
import { useSearchIndex } from '@/lib/api/hooks'
import { useRepositories } from '@/lib/api/hooks'
import { useAuth } from '@/lib/auth/use-auth'
import { filterRepositories, formatRelativeTime, isIngesting } from '@/lib/repo-utils'
import type { Repository } from '@/lib/types'
import { cn } from '@/lib/utils'

type Notice = { tone: 'success' | 'error'; text: string }

export function RepositoriesView() {
  const { data, error, isLoading, isValidating, mutate } = useRepositories()
  const { authenticated, loading: authLoading } = useAuth()
  const guest = !authLoading && !authenticated
  const [search, setSearch] = useState('')
  const [notice, setNotice] = useState<Notice | null>(null)
  const [reanalyzing, setReanalyzing] = useState<ReadonlySet<string>>(new Set())
  const [pendingRemoval, setPendingRemoval] = useState<Repository | null>(null)

  async function reanalyze(repo: Repository) {
    setNotice(null)
    setReanalyzing((current) => new Set(current).add(repo.id))
    try {
      await startIngestion(repo.id)
      setNotice({ tone: 'success', text: `Re-analysis of ${repo.fullName} started. The status updates here as it runs.` })
    } catch (err) {
      const { message, alreadyActive } = describeIngestionError(err)
      setNotice({ tone: alreadyActive ? 'success' : 'error', text: alreadyActive ? `${repo.fullName}: ${message}` : `Could not re-analyze ${repo.fullName}. ${message}` })
    } finally {
      setReanalyzing((current) => {
        const next = new Set(current)
        next.delete(repo.id)
        return next
      })
    }
  }

  const repositories = filterRepositories(data ?? [], search)

  return (
    <PageShell>
      <PageHeader
        title="Repositories"
        description={guest ? 'The demo repositories anyone can open.' : 'Open, re-analyze or remove the repositories in your workspace.'}
        actions={
          !guest && (
            <Link href="/dashboard" className={cn(buttonVariants({ variant: 'outline', size: 'sm' }))}>
              <Plus aria-hidden="true" />
              Import repository
            </Link>
          )
        }
      />

      {guest && (
        <div role="note" className="flex flex-col gap-3 rounded-lg border border-border bg-muted/30 p-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-muted-foreground">
            <span className="font-medium text-foreground">You are browsing the demo.</span> Sign in with GitHub to analyze your own repositories.
          </p>
          <GitHubSignIn next="/repositories" size="sm" className="shrink-0" />
        </div>
      )}

      <div className="flex h-9 max-w-sm items-center gap-2 rounded-md border border-border bg-card px-2.5 transition-colors focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/30">
        <Search className="size-4 text-muted-foreground" aria-hidden="true" />
        <label htmlFor="repo-filter" className="sr-only">
          Search repositories
        </label>
        <input
          id="repo-filter"
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by owner or name"
          className="h-full min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
        />
      </div>

      {notice && (
        <p
          className={cn(
            'flex items-start gap-2 rounded-md border px-3 py-2 text-sm',
            notice.tone === 'success' ? 'border-success/30 bg-success/10 text-success' : 'border-destructive/40 bg-destructive/10 text-destructive',
          )}
          role={notice.tone === 'error' ? 'alert' : 'status'}
        >
          {notice.tone === 'success' ? <CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden="true" /> : <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />}
          <span className="min-w-0 flex-1">{notice.text}</span>
          <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss message" className="rounded opacity-70 hover:opacity-100 focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/40">
            <X className="size-4" aria-hidden="true" />
          </button>
        </p>
      )}

      {isLoading ? (
        <>
          <ul className="divide-y divide-border rounded-lg border border-border bg-card" aria-busy="true" aria-label="Loading repositories">
            {Array.from({ length: 4 }, (_, i) => (
              <li key={i} className="flex items-center justify-between gap-4 px-4 py-3.5">
                <div className="flex flex-col gap-2">
                  <Skeleton className="h-4 w-48" />
                  <Skeleton className="h-3 w-64" />
                </div>
                <Skeleton className="h-7 w-40" />
              </li>
            ))}
          </ul>
          <SlowStartHint />
        </>
      ) : error && !data ? (
        <div role="alert" className="flex flex-col items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/5 p-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-destructive">{error instanceof Error ? error.message : 'Could not load repositories.'}</p>
          <Button size="sm" variant="outline" onClick={() => void mutate()} disabled={isValidating}>
            <RefreshCw className={isValidating ? 'animate-spin' : undefined} aria-hidden="true" />
            Try again
          </Button>
        </div>
      ) : repositories.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border px-6 py-12 text-center">
          <div className="flex size-10 items-center justify-center rounded-lg bg-muted">
            <FolderGit2 className="size-5 text-muted-foreground" aria-hidden="true" />
          </div>
          <p className="text-sm font-medium">{search.trim() ? 'No matching repositories' : guest ? 'No demo repositories yet' : 'No repositories yet'}</p>
          <p className="max-w-sm text-sm text-muted-foreground">
            {search.trim()
              ? 'Try a different owner or repository name.'
              : guest
                ? 'This server has not published any demo repositories.'
                : 'Import a public GitHub repository from the Dashboard to see it here.'}
          </p>
          {!search.trim() && !guest && (
            <Link href="/dashboard" className="mt-1 text-sm font-medium text-primary hover:underline">
              Go to Dashboard
            </Link>
          )}
        </div>
      ) : (
        <ul className="divide-y divide-border rounded-lg border border-border bg-card" aria-label="Repositories">
          {repositories.map((repo) => (
            <RepositoryRow
              key={repo.id}
              repository={repo}
              reanalyzing={reanalyzing.has(repo.id)}
              onReanalyze={() => void reanalyze(repo)}
              onRemove={() => setPendingRemoval(repo)}
            />
          ))}
        </ul>
      )}

      <RemoveRepositoryDialog
        repository={pendingRemoval}
        onClose={() => setPendingRemoval(null)}
        onRemoved={(repo) =>
          setNotice({ tone: 'success', text: `Removed ${repo.fullName} from your repositories. The repository on GitHub was not changed.` })
        }
      />
    </PageShell>
  )
}

function RepositoryRow({
  repository,
  reanalyzing,
  onReanalyze,
  onRemove,
}: {
  repository: Repository
  reanalyzing: boolean
  onReanalyze: () => void
  onRemove: () => void
}) {
  const { id, owner, name, fullName, status, progress, error, defaultBranch, indexedCommit, lastAnalyzedAt, truncated, viewerAccess } = repository
  const member = viewerAccess === 'member'
  const busy = isIngesting(status) || reanalyzing
  // Same readiness badge as the dashboard cards, fetched once per row (member-only on the server).
  const { data: index } = useSearchIndex(member && status === 'completed' ? repository : undefined, { poll: false })

  return (
    <li className="flex flex-col gap-3 px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex min-w-0 flex-col gap-1.5">
        <div className="flex min-w-0 items-center gap-2.5">
          <Link href={`/repos/${id}`} title={fullName} className="min-w-0 truncate rounded text-sm hover:underline focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/40">
            <span className="text-muted-foreground">{owner}/</span>
            <span className="font-semibold">{name}</span>
          </Link>
          <IndexingStatus status={status} progress={progress} className="shrink-0" />
        </div>
        {status === 'failed' && error ? (
          <p className="line-clamp-2 text-xs text-destructive/80" title={error}>
            {error}
          </p>
        ) : (
          <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
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
            <span>{lastAnalyzedAt ? `Ingested ${formatRelativeTime(lastAnalyzedAt)}` : 'Not ingested yet'}</span>
            {truncated && (
              <span className="inline-flex items-center gap-1 text-warning" title="The repository exceeded the ingestion limits, so only part of it is stored.">
                <TriangleAlert className="size-3.5" aria-hidden="true" />
                Partial snapshot
              </span>
            )}
            <IndexBadge index={index} />
            {!member && <span className="rounded border border-border px-1.5 py-0.5 text-[11px]">Demo · read-only</span>}
          </p>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-2">
        <Link href={`/repos/${id}`} className={cn(buttonVariants({ variant: 'outline', size: 'sm' }))}>
          Open
        </Link>
        {member && (
          <>
            <Button variant="outline" size="sm" onClick={onReanalyze} disabled={busy} aria-label={`Re-analyze ${fullName}`}>
              <RefreshCw className={busy ? 'animate-spin' : undefined} aria-hidden="true" />
              Re-analyze
            </Button>
            <Button variant="ghost" size="icon-sm" onClick={onRemove} aria-label={`Remove ${fullName}`} title="Remove" className="text-muted-foreground hover:text-destructive">
              <Trash2 aria-hidden="true" />
            </Button>
          </>
        )}
      </div>
    </li>
  )
}
