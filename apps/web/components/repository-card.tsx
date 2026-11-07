import Link from 'next/link'
import { Code2, MessageSquareText, Network } from 'lucide-react'
import { IndexingProgressBar, IndexingStatus } from '@/components/indexing-status'
import { formatRelativeTime } from '@/lib/repo-utils'
import type { Repository } from '@/lib/types'

const ACTION =
  'inline-flex items-center gap-1 rounded text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/40'
const ICON = 'size-3.5 opacity-80'

/**
 * `recent` (Dashboard) adds a quiet row of workspace shortcuts for ingested repositories. Failures show one short
 * line; the full error lives in the repository's own workspace.
 */
export function RepositoryCard({ repository, variant = 'library' }: { repository: Repository; variant?: 'recent' | 'library' }) {
  const { id, owner, name, description, primaryLanguage, status, progress, lastAnalyzedAt, error, fullName } = repository
  const failed = status === 'failed'

  return (
    <li className="relative flex flex-col gap-2 rounded-lg border border-border bg-card px-4 py-3 transition focus-within:border-primary/40 hover:-translate-y-px hover:border-primary/40 hover:bg-accent/30 focus-within:bg-accent/30 motion-reduce:transition-none motion-reduce:hover:translate-y-0">
      <div className="flex items-center justify-between gap-3">
        <Link
          href={`/repos/${id}`}
          title={fullName}
          className="min-w-0 rounded text-sm after:absolute after:inset-0 after:rounded-lg focus-visible:outline-none focus-visible:after:ring-3 focus-visible:after:ring-ring/40"
        >
          <span className="block truncate">
            <span className="text-muted-foreground">{owner}/</span>
            <span className="font-semibold">{name}</span>
          </span>
        </Link>
        <IndexingStatus status={status} progress={progress} className="shrink-0" />
      </div>

      {description && <p className="line-clamp-1 text-sm text-muted-foreground">{description}</p>}

      <IndexingProgressBar status={status} progress={progress} />

      <p className="truncate text-xs text-muted-foreground">
        {failed && error ? (
          <span className="text-destructive/70" title={error}>
            {error}
          </span>
        ) : (
          <>
            {primaryLanguage !== 'Unknown' && <>{primaryLanguage} · </>}
            {lastAnalyzedAt ? `Ingested ${formatRelativeTime(lastAnalyzedAt)}` : 'Not ingested yet'}
          </>
        )}
      </p>

      {variant === 'recent' && status === 'completed' && (
        <div className="relative z-10 flex gap-3">
          <Link href={`/repos/${id}?tab=ask`} className={`${ACTION} !text-primary hover:!text-primary/80`}>
            <MessageSquareText className={ICON} aria-hidden="true" />
            Ask AI
          </Link>
          <Link href={`/repos/${id}?tab=code`} className={ACTION}>
            <Code2 className={ICON} aria-hidden="true" />
            Explore Code
          </Link>
          <Link href={`/repos/${id}/graph`} className={ACTION}>
            <Network className={ICON} aria-hidden="true" />
            Architecture
          </Link>
        </div>
      )}
    </li>
  )
}
