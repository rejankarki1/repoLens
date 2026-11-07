'use client'

import Link from 'next/link'
import { FolderGit2 } from 'lucide-react'
import { GitHubSignIn } from '@/components/auth/github-sign-in'
import { RepositoryCard } from '@/components/repository-card'
import { Skeleton } from '@/components/ui/skeleton'
import { useRepositories } from '@/lib/api/hooks'
import { useAuth } from '@/lib/auth/use-auth'

export function RepositoryList({ limit, filter = '', variant = 'library' }: { limit?: number; filter?: string; variant?: 'recent' | 'library' }) {
  const { data, error, isLoading } = useRepositories()
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

  if (error) {
    return <p className="rounded-lg border border-destructive/40 p-4 text-sm text-destructive">Could not load repositories.</p>
  }

  const q = filter.trim().toLowerCase()
  const repositories = (data ?? []).filter((r) => !q || r.fullName.toLowerCase().includes(q)).slice(0, limit)

  const guestNotice = guest && (
    <div role="note" className="flex flex-col gap-3 rounded-lg border border-border bg-muted/30 p-4 sm:flex-row sm:items-center sm:justify-between">
      <p className="text-sm text-muted-foreground">
        <span className="font-medium text-foreground">You are browsing the showcase.</span> These repositories are open to
        everyone to explore. Sign in with GitHub to analyze your own and ask questions.
      </p>
      <GitHubSignIn next="/repositories" size="sm" className="shrink-0" />
    </div>
  )

  if (repositories.length === 0) {
    return guest ? (
      <div className="flex flex-col gap-3">
        {guestNotice}
        <p className="text-sm text-muted-foreground">No showcase repositories are available yet.</p>
      </div>
    ) : (
      <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border px-6 py-12 text-center">
        <div className="flex size-10 items-center justify-center rounded-lg bg-muted">
          <FolderGit2 className="size-5 text-muted-foreground" aria-hidden="true" />
        </div>
        <p className="text-sm font-medium">{q ? 'No matching repositories' : 'No repositories yet'}</p>
        <p className="max-w-sm text-sm text-muted-foreground">
          {q ? 'Try a different owner or repository name.' : 'Import a GitHub repository from the Dashboard to see it here.'}
        </p>
        {!q && (
          <Link href="/dashboard" className="mt-1 text-sm font-medium text-primary hover:underline">
            Go to Dashboard
          </Link>
        )}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      {guestNotice}
      <ul className="grid items-start gap-3 md:grid-cols-2">
        {repositories.map((repo) => (
          <RepositoryCard key={repo.id} repository={repo} variant={variant} />
        ))}
      </ul>
    </div>
  )
}
