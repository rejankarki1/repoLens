'use client'

import Link from 'next/link'
import { Code2, GitCommitHorizontal, MessageSquareText, Network, Sparkles } from 'lucide-react'
import { IndexingStatus } from '@/components/indexing-status'
import { buttonVariants } from '@/components/ui/button'
import { demoDescription } from '@/lib/demo-repo-descriptions'
import { useGuestAsk } from '@/lib/api/hooks'
import type { Repository } from '@/lib/types'
import { cn } from '@/lib/utils'

const SECONDARY = cn(buttonVariants({ variant: 'outline', size: 'sm' }))

/**
 * A showcase repository, for a visitor: what it is and what you can do with it, no workspace required. Leads
 * with the description and Ask AI (the feature guests can try live); branch/commit are a quiet detail line,
 * not the headline. Used on the landing page and the guest dashboard, so both describe the demo the same way.
 */
export function DemoRepositoryCard({ repo }: { repo: Repository }) {
  const { data: guestAsk } = useGuestAsk(repo.id)
  const about = demoDescription(repo.fullName)

  return (
    <li className="flex flex-col gap-3 rounded-lg border border-border bg-card p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Link
          href={`/repos/${repo.id}`}
          className="min-w-0 truncate rounded text-base hover:underline focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/40"
        >
          <span className="text-muted-foreground">{repo.owner}/</span>
          <span className="font-semibold">{repo.name}</span>
        </Link>
        <span className="inline-flex items-center gap-1 rounded bg-primary/10 px-1.5 py-0.5 text-[11px] font-medium text-primary">
          <Sparkles className="size-3" aria-hidden="true" />
          Demo
        </span>
      </div>

      {about && (
        <div className="flex flex-col gap-1.5">
          <p className="text-sm leading-relaxed text-muted-foreground">{about.summary}</p>
          <p className="font-mono text-xs text-muted-foreground/70">{about.stack}</p>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground/70">
        <IndexingStatus status={repo.status} progress={repo.progress} />
        {repo.indexedCommit && (
          <span className="inline-flex items-center gap-1" title="Ingested commit">
            <GitCommitHorizontal className="size-3.5" aria-hidden="true" />
            <span className="font-mono">{repo.indexedCommit}</span>
          </span>
        )}
      </div>

      {repo.indexedCommit && (
        <div className="mt-1 flex flex-wrap gap-2">
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
