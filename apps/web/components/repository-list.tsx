'use client'

import { FolderGit2, RefreshCw } from 'lucide-react'
import { GitHubSignIn } from '@/components/auth/github-sign-in'
import { RepositoryCard } from '@/components/repository-card'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useRepositories } from '@/lib/api/hooks'
import { useAuth } from '@/lib/auth/use-auth'

/** The newest repositories as cards (Dashboard). The full management list lives on the Repositories page. */
export function RepositoryList({ limit }: { limit?: number }) {
  const { data, error, isLoading, isValidating, mutate } = useRepositories()
  const { authenticated, loading: authLoading } = useAuth()
  const guest = !authLoading && !authenticated

  if (isLoading) {
    return (
      <ul className="grid gap-3 md:grid-cols-2" aria-busy="true" aria-label="Loading repositories">
        {Array.from({ length: 4 }, (_, i) => (
          <li key={i} className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-6 w-48" />
          </li>
        ))}
      </ul>
    )
  }

  if (error && !data) {
    return (
      <div role="alert" className="flex flex-col items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/5 p-4 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm text-destructive">{error instanceof Error ? error.message : 'Could not load your repositories.'}</p>
        <Button size="sm" variant="outline" onClick={() => void mutate()} disabled={isValidating}>
          <RefreshCw className={isValidating ? 'animate-spin' : undefined} aria-hidden="true" />
          Try again
        </Button>
      </div>
    )
  }

  const repositories = (data ?? []).slice(0, limit)

  const guestNotice = guest && (
    <div role="note" className="flex flex-col gap-3 rounded-lg border border-border bg-muted/30 p-4 sm:flex-row sm:items-center sm:justify-between">
      <p className="text-sm text-muted-foreground">
        <span className="font-medium text-foreground">You are browsing the demo.</span> These repositories are open to everyone to explore.
        Sign in with GitHub to analyze your own and ask questions.
      </p>
      <GitHubSignIn next="/dashboard" size="sm" className="shrink-0" />
    </div>
  )

  if (repositories.length === 0) {
    return guest ? (
      <div className="flex flex-col gap-3">
        {guestNotice}
        <p className="text-sm text-muted-foreground">No demo repositories are available on this server yet.</p>
      </div>
    ) : (
      <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border px-6 py-10 text-center">
        <div className="flex size-10 items-center justify-center rounded-lg bg-muted">
          <FolderGit2 className="size-5 text-muted-foreground" aria-hidden="true" />
        </div>
        <p className="text-sm font-medium">No repositories yet</p>
        <p className="max-w-sm text-sm text-muted-foreground">
          Paste a public GitHub URL above. RepoLens downloads it, indexes the code, and you can start asking questions in a minute or two.
        </p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      {guestNotice}
      <ul className="grid items-start gap-3 md:grid-cols-2">
        {repositories.map((repo) => (
          <RepositoryCard key={repo.id} repository={repo} />
        ))}
      </ul>
    </div>
  )
}
