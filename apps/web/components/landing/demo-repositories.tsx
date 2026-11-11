'use client'

import Link from 'next/link'
import { Code2, GitBranch, GitCommitHorizontal, MessageSquareText, Network } from 'lucide-react'
import { IndexingStatus } from '@/components/indexing-status'
import { buttonVariants } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useGuestAsk, useRepositories } from '@/lib/api/hooks'
import { useAuth } from '@/lib/auth/use-auth'
import type { Repository } from '@/lib/types'
import { cn } from '@/lib/utils'

/** What each demo repository is, written from its own README (the API does not carry descriptions). */
const ABOUT: Record<string, { summary: string; stack: string }> = {
  'rejankarki1/classlens': {
    summary: 'A mobile notebook for college students. It turns photos of lecture slides and handwritten notes into organized course material, with grounded Q&A and quizzes.',
    stack: 'Expo · React Native · Supabase · Gemini',
  },
  'rejankarki1/wanderlust': {
    summary: 'A full-stack travel-listing app: browse and publish stays, reserve trips, write reviews, and see each listing on a map.',
    stack: 'React · Express · MongoDB · Mapbox',
  },
}

const SECONDARY = cn(buttonVariants({ variant: 'outline', size: 'sm' }))

function DemoCard({ repo }: { repo: Repository }) {
  const { data: guestAsk } = useGuestAsk(repo.id)
  const about = ABOUT[repo.fullName.toLowerCase()]
  return (
    <li className="flex flex-col gap-4 rounded-lg border border-border bg-card p-5">
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-3">
          <Link href={`/repos/${repo.id}`} className="min-w-0 truncate rounded text-base hover:underline focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/40">
            <span className="text-muted-foreground">{repo.owner}/</span>
            <span className="font-semibold">{repo.name}</span>
          </Link>
          <IndexingStatus status={repo.status} progress={repo.progress} className="shrink-0" />
        </div>
        {about && (
          <>
            <p className="text-sm leading-relaxed text-muted-foreground">{about.summary}</p>
            <p className="font-mono text-xs text-muted-foreground/80">{about.stack}</p>
          </>
        )}
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
      </div>
      {repo.indexedCommit && (
        <div className="mt-auto flex flex-wrap gap-2">
          <Link href={`/repos/${repo.id}?tab=ask`} className={cn(buttonVariants({ size: 'sm' }))}>
            <MessageSquareText aria-hidden="true" />
            {guestAsk?.enabled ? 'Ask AI' : 'See example answers'}
          </Link>
          <Link href={`/repos/${repo.id}?tab=code`} className={SECONDARY}>
            <Code2 aria-hidden="true" />
            Explore Code
          </Link>
          <Link href={`/repos/${repo.id}/graph`} className={SECONDARY}>
            <Network aria-hidden="true" />
            Architecture
          </Link>
        </div>
      )}
    </li>
  )
}

/** The demo repositories the server publishes (the guest showcase), read from the API. Hidden when there are none. */
export function DemoRepositories() {
  const { data, isLoading } = useRepositories()
  const { authenticated } = useAuth()
  const first = data?.[0]
  const { data: guestAsk } = useGuestAsk(first?.id ?? '', !!first)
  if (authenticated || (!isLoading && (!data || data.length === 0))) return null

  return (
    <section id="demo" aria-labelledby="demo-heading" className="flex scroll-mt-8 flex-col gap-5">
      <div className="flex flex-col gap-1.5">
        <h2 id="demo-heading" className="text-2xl font-semibold tracking-tight">
          Try it on a real repository
        </h2>
        <p className="max-w-2xl text-muted-foreground">
          {guestAsk?.enabled
            ? `Both repositories are already indexed. Without signing in, you can ask up to ${guestAsk.limit} real questions a day about them, answered from the code with cited sources. Example answers, the code and the architecture are always open.`
            : 'Both repositories are already indexed. Browse the code and the architecture, and read example questions with their cited sources. No account needed.'}
        </p>
      </div>
      <ul className="grid gap-4 sm:grid-cols-2">
        {isLoading ? Array.from({ length: 2 }, (_, i) => <Skeleton key={i} className="h-52 rounded-lg" />) : data!.slice(0, 2).map((repo) => <DemoCard key={repo.id} repo={repo} />)}
      </ul>
    </section>
  )
}
