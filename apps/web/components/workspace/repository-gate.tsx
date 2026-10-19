'use client'

import Link from 'next/link'
import { AlertCircle, FolderX, Loader2 } from 'lucide-react'
import { IndexingProgressBar } from '@/components/indexing-status'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { isIngestionRunning, startIngestion } from '@/lib/api/client'
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

  if (repo.status === 'failed') {
    return (
      <StateMessage icon={<AlertCircle className="size-5 text-destructive" aria-hidden="true" />} title="Analysis failed">
        {repo.error ?? 'Something went wrong while ingesting this repository.'} Use Re-analyze above to try again.
      </StateMessage>
    )
  }

  if (repo.status === 'queued' && !isIngestionRunning(repo.id)) {
    return (
      <StateMessage icon={<Loader2 className="size-5 text-muted-foreground" aria-hidden="true" />} title="Not ingested yet">
        Source files have not been downloaded for {repo.fullName}.
        <Button
          size="sm"
          className="mt-2"
          onClick={() => {
            startIngestion(repo.id).catch(() => undefined)
          }}
        >
          Start ingestion
        </Button>
      </StateMessage>
    )
  }

  if (repo.status !== 'completed') {
    return (
      <div className="flex justify-center px-4 py-16">
        <div className="flex w-full max-w-md flex-col gap-5 rounded-lg border border-border bg-card p-6">
          <div className="flex flex-col gap-1">
            <p className="text-sm font-medium">Ingesting {repo.fullName}</p>
            <p className="text-sm text-muted-foreground">
              Downloading the repository from GitHub and storing its source files. The workspace unlocks automatically
              when this finishes.
            </p>
          </div>
          <IndexingProgressBar status="indexing" progress={null} />
        </div>
      </div>
    )
  }

  return <>{children(repo)}</>
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
