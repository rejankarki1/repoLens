'use client'

import { ArrowRight, ListChecks, MessageSquareText, RefreshCw, ScanSearch } from 'lucide-react'
import { RepositoryCard } from '@/components/repository-card'
import { SlowStartHint } from '@/components/slow-start-hint'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useRepositories } from '@/lib/api/hooks'
import { sortByRecent } from '@/lib/recent-repos'
import { useRecentlyOpened } from '@/lib/use-recently-opened'

/** The repositories you most recently opened (Dashboard, signed in). Only ever rendered for a signed-in
 * member: `/dashboard` picks `GuestDashboard` instead for a visitor, so there is no guest state to handle here. */
export function RepositoryList({ limit }: { limit?: number }) {
  const { data, error, isLoading, isValidating, mutate } = useRepositories()
  const opened = useRecentlyOpened()

  if (isLoading) {
    return (
      <>
        <ul className="grid gap-3 md:grid-cols-2" aria-busy="true" aria-label="Loading repositories">
          {Array.from({ length: 4 }, (_, i) => (
            <li key={i} className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-6 w-48" />
            </li>
          ))}
        </ul>
        <SlowStartHint />
      </>
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

  const repositories = sortByRecent(data ?? [], opened).slice(0, limit)

  if (repositories.length === 0) return <GettingStarted />

  return (
    <ul className="grid items-start gap-3 md:grid-cols-2">
      {repositories.map((repo) => (
        <RepositoryCard key={repo.id} repository={repo} openedAt={opened[repo.id]} />
      ))}
    </ul>
  )
}

const STEPS = [
  { icon: ListChecks, title: 'Paste a GitHub URL', text: 'Any public repository. RepoLens only reads it; your code on GitHub is never changed.' },
  { icon: ScanSearch, title: 'Wait for ingestion', text: 'It downloads and indexes the code, usually within a minute. You can leave the page; progress shows on the repository card.' },
  { icon: MessageSquareText, title: 'Ask, explore, verify', text: 'Ask questions, browse the code and see how the modules connect. Every answer cites the files and lines it used.' },
]

/** The empty dashboard: what to do, what will happen, and what the result is. */
function GettingStarted() {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">You have no repositories yet. Add your first one above. Here is what happens next:</p>
      <ol className="grid gap-3 md:grid-cols-3">
        {STEPS.map(({ icon: Icon, title, text }, i) => (
          <li key={title} className="flex flex-col gap-2 rounded-lg border border-border bg-card p-4">
            <div className="flex items-center gap-2.5">
              <span className="flex size-7 items-center justify-center rounded-md bg-primary/10 text-primary">
                <Icon className="size-4" aria-hidden="true" />
              </span>
              <span className="font-mono text-xs text-muted-foreground">Step {i + 1}</span>
            </div>
            <h3 className="text-sm font-medium">{title}</h3>
            <p className="text-xs leading-relaxed text-muted-foreground">{text}</p>
          </li>
        ))}
      </ol>
      <a href="#repo-url" className="inline-flex w-fit items-center gap-1.5 text-sm font-medium text-primary hover:underline">
        Go to the import box
        <ArrowRight className="size-3.5" aria-hidden="true" />
      </a>
    </div>
  )
}
