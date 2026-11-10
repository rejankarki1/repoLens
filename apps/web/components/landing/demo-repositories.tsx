'use client'

import Link from 'next/link'
import { Code2, GitBranch, GitCommitHorizontal, MessageSquareText, Network } from 'lucide-react'
import { IndexingStatus } from '@/components/indexing-status'
import { Skeleton } from '@/components/ui/skeleton'
import { useRepositories } from '@/lib/api/hooks'
import { useAuth } from '@/lib/auth/use-auth'

const ACTION =
  'inline-flex h-7 items-center gap-1.5 rounded-md border border-border bg-background px-2 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/40'

/** The demo repositories the server publishes (the guest showcase), read from the API. Hidden when there are none. */
export function DemoRepositories() {
  const { data, isLoading } = useRepositories()
  const { authenticated } = useAuth()
  if (authenticated || (!isLoading && (!data || data.length === 0))) return null

  return (
    <section aria-labelledby="demo-heading" className="flex flex-col gap-5">
      <div className="flex flex-col gap-1.5">
        <h2 id="demo-heading" className="text-2xl font-semibold tracking-tight">
          Try it on a real repository
        </h2>
        <p className="max-w-2xl text-muted-foreground">
          These repositories are already indexed. Browse the code and the architecture, and read example questions with their cited sources. No
          account needed.
        </p>
      </div>
      <ul className="grid gap-3 sm:grid-cols-2">
        {isLoading
          ? Array.from({ length: 2 }, (_, i) => <Skeleton key={i} className="h-32 rounded-lg" />)
          : [...data!].reverse().slice(0, 2).map((repo) => (
              <li key={repo.id} className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
                <div className="flex items-center justify-between gap-3">
                  <Link href={`/repos/${repo.id}`} className="min-w-0 truncate rounded text-sm hover:underline focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/40">
                    <span className="text-muted-foreground">{repo.owner}/</span>
                    <span className="font-semibold">{repo.name}</span>
                  </Link>
                  <IndexingStatus status={repo.status} progress={repo.progress} className="shrink-0" />
                </div>
                <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                  <span className="inline-flex items-center gap-1">
                    <GitBranch className="size-3.5" aria-hidden="true" />
                    <span className="font-mono">{repo.defaultBranch}</span>
                  </span>
                  {repo.indexedCommit && (
                    <span className="inline-flex items-center gap-1">
                      <GitCommitHorizontal className="size-3.5" aria-hidden="true" />
                      <span className="font-mono">{repo.indexedCommit}</span>
                    </span>
                  )}
                </p>
                {repo.indexedCommit && (
                  <div className="flex flex-wrap gap-2">
                    <Link href={`/repos/${repo.id}?tab=ask`} className={ACTION}>
                      <MessageSquareText className="size-3.5" aria-hidden="true" />
                      Example answers
                    </Link>
                    <Link href={`/repos/${repo.id}?tab=code`} className={ACTION}>
                      <Code2 className="size-3.5" aria-hidden="true" />
                      Explore Code
                    </Link>
                    <Link href={`/repos/${repo.id}/graph`} className={ACTION}>
                      <Network className="size-3.5" aria-hidden="true" />
                      Architecture
                    </Link>
                  </div>
                )}
              </li>
            ))}
      </ul>
    </section>
  )
}
