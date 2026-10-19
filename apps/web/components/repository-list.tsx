'use client'

import { FolderGit2 } from 'lucide-react'
import { RepositoryCard } from '@/components/repository-card'
import { Skeleton } from '@/components/ui/skeleton'
import { useRepositories } from '@/lib/api/hooks'

export function RepositoryList({ limit, filter = '' }: { limit?: number; filter?: string }) {
  const { data, error, isLoading } = useRepositories()

  if (isLoading) {
    return (
      <ul className="grid gap-3 lg:grid-cols-2" aria-busy="true" aria-label="Loading repositories">
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

  if (repositories.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border px-6 py-12 text-center">
        <div className="flex size-10 items-center justify-center rounded-lg bg-muted">
          <FolderGit2 className="size-5 text-muted-foreground" aria-hidden="true" />
        </div>
        <p className="text-sm font-medium">{q ? 'No matching repositories' : 'No repositories yet'}</p>
        <p className="max-w-sm text-sm text-muted-foreground">
          {q
            ? 'Try a different owner or repository name.'
            : 'Paste a GitHub repository URL above to import and index it.'}
        </p>
      </div>
    )
  }

  return (
    <ul className="grid gap-3 lg:grid-cols-2">
      {repositories.map((repo) => (
        <RepositoryCard key={repo.id} repository={repo} />
      ))}
    </ul>
  )
}
