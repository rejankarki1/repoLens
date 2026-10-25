import { AlertCircle, CheckCircle2, Clock, Loader2 } from 'lucide-react'
import type { IndexingState } from '@/lib/types'
import { cn } from '@/lib/utils'

const CONFIG: Record<IndexingState, { label: string; className: string; icon: typeof Clock; spin?: boolean }> = {
  pending: { label: 'Not ingested', className: 'text-muted-foreground bg-muted', icon: Clock },
  queued: { label: 'Queued', className: 'text-muted-foreground bg-muted', icon: Clock },
  importing: { label: 'Importing', className: 'text-primary bg-primary/10', icon: Loader2, spin: true },
  indexing: { label: 'Ingesting', className: 'text-warning bg-warning/10', icon: Loader2, spin: true },
  completed: { label: 'Ingested', className: 'text-success bg-success/10', icon: CheckCircle2 },
  failed: { label: 'Failed', className: 'text-destructive bg-destructive/10', icon: AlertCircle },
}

interface IndexingStatusProps {
  status: IndexingState
  progress?: number | null
  className?: string
}

export function IndexingStatus({ status, progress, className }: IndexingStatusProps) {
  const { label, className: tone, icon: Icon, spin } = CONFIG[status]
  const showProgress = (status === 'importing' || status === 'indexing') && typeof progress === 'number'

  return (
    <span
      className={cn('inline-flex h-6 items-center gap-1.5 rounded-md px-2 text-xs font-medium', tone, className)}
      role="status"
    >
      <Icon className={cn('size-3.5', spin && 'animate-spin')} aria-hidden="true" />
      {label}
      {showProgress && <span className="font-mono tabular-nums opacity-80">{progress}%</span>}
    </span>
  )
}

export function IndexingProgressBar({ status, progress }: { status: IndexingState; progress: number | null }) {
  if (status !== 'importing' && status !== 'indexing') return null
  if (progress === null) {
    // No percentage from the backend: show an indeterminate bar.
    return (
      <div
        className="h-1 w-full overflow-hidden rounded-full bg-muted"
        role="progressbar"
        aria-busy="true"
        aria-label="Ingesting source files"
      >
        <div className="h-full w-1/3 animate-pulse rounded-full bg-warning" />
      </div>
    )
  }
  return (
    <div
      className="h-1 w-full overflow-hidden rounded-full bg-muted"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={progress}
      aria-label={status === 'importing' ? 'Import progress' : 'Indexing progress'}
    >
      <div
        className={cn('h-full rounded-full transition-[width] duration-300', status === 'importing' ? 'bg-primary' : 'bg-warning')}
        style={{ width: `${progress}%` }}
      />
    </div>
  )
}
