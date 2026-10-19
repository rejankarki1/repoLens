import Link from 'next/link'
import { ChevronRight } from 'lucide-react'
import { IndexingProgressBar, IndexingStatus } from '@/components/indexing-status'
import { LanguageDot } from '@/components/language-dot'
import type { Repository } from '@/lib/types'
import { formatRelativeTime } from '@/lib/repo-utils'

export function RepositoryCard({ repository }: { repository: Repository }) {
  const { id, owner, name, description, primaryLanguage, status, progress, lastAnalyzedAt, error } = repository

  return (
    <li>
      <Link
        href={`/repos/${id}`}
        className="group flex flex-col gap-3 rounded-lg border border-border bg-card p-4 transition-colors hover:border-primary/40 hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/40"
      >
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm">
              <span className="text-muted-foreground">{owner}/</span>
              <span className="font-semibold">{name}</span>
            </p>
            <p className="mt-1 line-clamp-2 text-sm leading-relaxed text-muted-foreground">
              {status === 'failed' && error ? <span className="text-destructive">{error}</span> : description}
            </p>
          </div>
          <ChevronRight
            className="mt-0.5 size-4 shrink-0 text-muted-foreground opacity-0 transition-all group-hover:translate-x-0.5 group-hover:opacity-100"
            aria-hidden="true"
          />
        </div>

        <IndexingProgressBar status={status} progress={progress} />

        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
          <IndexingStatus status={status} progress={progress} />
          {primaryLanguage !== 'Unknown' && (
            <span className="inline-flex items-center gap-1.5">
              <LanguageDot language={primaryLanguage} />
              {primaryLanguage}
            </span>
          )}
          <span className="ml-auto">
            {lastAnalyzedAt ? `Ingested ${formatRelativeTime(lastAnalyzedAt)}` : 'Not ingested yet'}
          </span>
        </div>
      </Link>
    </li>
  )
}
