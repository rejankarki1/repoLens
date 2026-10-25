'use client'

import Link from 'next/link'
import { useState } from 'react'
import { AlertCircle, Clock, FolderX, Loader2 } from 'lucide-react'
import { IndexingProgressBar } from '@/components/indexing-status'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { describeIngestionError, startIngestion } from '@/lib/api/client'
import { useRepository } from '@/lib/api/hooks'
import type { Repository } from '@/lib/types'

/**
 * Renders children only once the repository is fully indexed, and shows
 * loading, progress, failure, and not-found states otherwise.
 */
export function RepositoryGate({ id, children }: { id: string; children: (repo: Repository) => React.ReactNode }) {
  const { data: repo, error, isLoading } = useRepository(id)

  if (isLoading) {
    return (
      <div className="flex flex-col gap-4 p-6">
        <Skeleton className="h-5 w-64" />
        <Skeleton className="h-24 w-full max-w-3xl" />
        <Skeleton className="h-24 w-full max-w-3xl" />
      </div>
    )
  }

  if (error || !repo) {
    return (
      <StateMessage icon={<FolderX className="size-5 text-muted-foreground" aria-hidden="true" />} title="Repository not found">
        This repository is not in your workspace, or the RepoLens API could not be reached.
        <Button size="sm" variant="outline" className="mt-2" render={<Link href="/dashboard" />} nativeButton={false}>
          Back to dashboard
        </Button>
      </StateMessage>
    )
  }

  // A re-ingestion is running or failed, but a previous snapshot is stored and still served by the
  // backend: keep the workspace usable and explain the state in a banner.
  if (repo.indexedCommit && repo.status !== 'completed' && repo.status !== 'pending') {
    return (
      <div className="flex h-full min-h-0 flex-col">
        <SnapshotBanner repo={repo} />
        <div className="min-h-0 flex-1">{children(repo)}</div>
      </div>
    )
  }

  if (repo.status === 'failed') {
    return (
      <StateMessage icon={<AlertCircle className="size-5 text-destructive" aria-hidden="true" />} title="Analysis failed">
        {repo.error ?? 'Something went wrong while ingesting this repository.'} Use Re-analyze above to try again.
      </StateMessage>
    )
  }

  if (repo.status === 'pending') {
    return (
      <StateMessage icon={<Clock className="size-5 text-muted-foreground" aria-hidden="true" />} title="Not ingested yet">
        Source files have not been downloaded for {repo.fullName}.
        <StartIngestionButton id={repo.id} />
      </StateMessage>
    )
  }

  if (repo.status !== 'completed') {
    const queued = repo.status === 'queued'
    return (
      <div className="flex justify-center px-4 py-16">
        <div className="flex w-full max-w-md flex-col gap-5 rounded-lg border border-border bg-card p-6">
          <div className="flex flex-col gap-1">
            <p className="text-sm font-medium">{queued ? `Queued ${repo.fullName}` : `Ingesting ${repo.fullName}`}</p>
            <p className="text-sm text-muted-foreground">
              {queued
                ? 'Waiting for a background worker to pick this up.'
                : 'Downloading the repository from GitHub and storing its source files.'}{' '}
              This runs in the background, so you can leave this page; the workspace unlocks automatically when it
              finishes.
            </p>
          </div>
          <IndexingProgressBar status="indexing" progress={null} />
        </div>
      </div>
    )
  }

  return <>{children(repo)}</>
}

function SnapshotBanner({ repo }: { repo: Repository }) {
  const failed = repo.status === 'failed'
  return (
    <div
      role={failed ? 'alert' : 'status'}
      className={
        failed
          ? 'flex items-start gap-2 border-b border-warning/40 bg-warning/10 px-4 py-2 text-xs text-warning sm:px-6'
          : 'flex items-start gap-2 border-b border-border bg-muted/40 px-4 py-2 text-xs text-muted-foreground sm:px-6'
      }
    >
      {failed ? (
        <AlertCircle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
      ) : (
        <Loader2 className="mt-0.5 size-3.5 shrink-0 animate-spin" aria-hidden="true" />
      )}
      <p>
        {failed
          ? `The latest ingestion failed${repo.error ? `: ${repo.error}` : '.'} Showing files from commit ${repo.indexedCommit}. Use Re-analyze to try again.`
          : `Updating in the background. Showing files from commit ${repo.indexedCommit} until it finishes.`}
      </p>
    </div>
  )
}

function StartIngestionButton({ id }: { id: string }) {
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleClick() {
    setSubmitting(true)
    setError(null)
    try {
      await startIngestion(id)
    } catch (err) {
      const { message, alreadyActive } = describeIngestionError(err)
      // A 409 means it is already queued or running; the refreshed status will show that.
      if (!alreadyActive) setError(message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      <Button size="sm" className="mt-2" onClick={handleClick} disabled={submitting}>
        {submitting && <Loader2 className="animate-spin" aria-hidden="true" />}
        Start ingestion
      </Button>
      {error && (
        <p role="alert" className="mt-2 text-destructive">
          {error}
        </p>
      )}
    </>
  )
}

function StateMessage({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-20 text-center">
      <div className="flex size-10 items-center justify-center rounded-lg bg-muted">{icon}</div>
      <p className="text-sm font-medium">{title}</p>
      <div className="flex max-w-md flex-col items-center text-sm text-muted-foreground">{children}</div>
    </div>
  )
}
