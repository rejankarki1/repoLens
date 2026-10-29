'use client'

import { useEffect, useState } from 'react'
import { AlertCircle, CheckCircle2, Clock, Loader2, Network, RefreshCw, TriangleAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { AnalysisStatus } from '@/lib/api/architecture'
import { shortSha } from '@/lib/architecture/model'
import { formatRelativeTime } from '@/lib/repo-utils'
import { cn } from '@/lib/utils'

/** A queued job older than this probably has no worker to run it. */
const SLOW_QUEUE_MS = 20_000

function useAgeMs(since: string | null): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 5_000)
    return () => clearInterval(timer)
  }, [])
  return since ? Math.max(0, now - new Date(since).getTime()) : 0
}

export function StatusStrip({
  status, onRerun, rerunning,
}: {
  status: AnalysisStatus
  onRerun: () => void
  rerunning: boolean
}) {
  const active = status.status === 'queued' || status.status === 'running'
  const chip =
    status.status === 'running' || status.status === 'queued'
      ? { icon: Loader2, spin: true, text: status.status === 'queued' ? 'Queued' : 'Analyzing…', tone: 'text-primary bg-primary/10' }
      : status.status === 'failed'
        ? { icon: AlertCircle, spin: false, text: 'Last analysis failed', tone: 'text-destructive bg-destructive/10' }
        : status.freshness === 'current'
          ? { icon: CheckCircle2, spin: false, text: 'Up to date', tone: 'text-success bg-success/10' }
          : { icon: Clock, spin: false, text: 'Out of date', tone: 'text-warning bg-warning/10' }
  const Icon = chip.icon

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border px-4 py-2 text-xs sm:px-6">
      <span role="status" className={cn('inline-flex h-6 items-center gap-1.5 rounded-md px-2 font-medium', chip.tone)}>
        <Icon className={cn('size-3.5', chip.spin && 'animate-spin')} aria-hidden="true" />
        {chip.text}
      </span>
      {status.has_result && (
        <span className="text-muted-foreground">
          Showing commit <code className="font-mono text-foreground">{shortSha(status.analyzed_commit_sha)}</code>
          {status.analyzed_at && <> · analyzed {formatRelativeTime(status.analyzed_at)}</>}
        </span>
      )}
      <Button size="xs" variant="outline" className="ml-auto" onClick={onRerun} disabled={active || rerunning}>
        <RefreshCw className={cn(rerunning && 'animate-spin')} aria-hidden="true" />
        Re-run analysis
      </Button>
    </div>
  )
}

/** Notices that sit above an existing graph: stale, updating, failed. */
export function StatusBanners({ status, requestError }: { status: AnalysisStatus; requestError: string | null }) {
  const active = status.status === 'queued' || status.status === 'running'
  const stale = status.has_result && status.freshness === 'stale'
  const items: { key: string; tone: 'info' | 'warning' | 'danger'; text: string }[] = []

  if (requestError) items.push({ key: 'request', tone: 'danger', text: `Could not start the analysis: ${requestError}` })
  if (status.status === 'failed' && status.has_result) {
    items.push({
      key: 'failed',
      tone: 'danger',
      text: `The latest analysis failed${status.error ? `: ${status.error.replace(/[.\s]+$/, '')}` : ''}. The graph below is the last successful one, for commit ${shortSha(status.analyzed_commit_sha)}.`,
    })
  } else if (stale && active) {
    items.push({
      key: 'updating',
      tone: 'info',
      text: `Updating for commit ${shortSha(status.current_commit_sha)}. Until it finishes, this is the analysis of commit ${shortSha(status.analyzed_commit_sha)}.`,
    })
  } else if (stale) {
    items.push({
      key: 'stale',
      tone: 'warning',
      text: `This analysis is for commit ${shortSha(status.analyzed_commit_sha)}, but the repository is now at ${shortSha(status.current_commit_sha)}. Re-run the analysis to update it.`,
    })
  } else if (active && status.has_result) {
    items.push({ key: 'refreshing', tone: 'info', text: 'Re-running the analysis. This graph stays available until it finishes.' })
  }
  if (items.length === 0) return null
  return (
    <div className="flex flex-col">
      {items.map((b) => (
        <p
          key={b.key}
          role={b.tone === 'danger' ? 'alert' : 'status'}
          className={cn(
            'flex items-start gap-2 border-b px-4 py-2 text-xs sm:px-6',
            b.tone === 'danger' && 'border-destructive/40 bg-destructive/10 text-destructive',
            b.tone === 'warning' && 'border-warning/40 bg-warning/10 text-warning',
            b.tone === 'info' && 'border-border bg-muted/40 text-muted-foreground',
          )}
        >
          {b.tone === 'info' ? (
            <Loader2 className="mt-0.5 size-3.5 shrink-0 animate-spin" aria-hidden="true" />
          ) : (
            <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          )}
          {b.text}
        </p>
      ))}
    </div>
  )
}

function Centered({ icon, title, children }: { icon: React.ReactNode; title: string; children?: React.ReactNode }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 px-6 py-16 text-center">
      <div className="flex size-10 items-center justify-center rounded-lg bg-muted">{icon}</div>
      <p className="text-sm font-medium">{title}</p>
      <div className="flex max-w-md flex-col items-center gap-3 text-sm text-muted-foreground">{children}</div>
    </div>
  )
}

/** No result yet: the analysis is being requested, queued or running. */
export function PendingState({ status }: { status: AnalysisStatus | null }) {
  const queued = status?.status === 'queued'
  const age = useAgeMs(queued ? status.requested_at : null)
  return (
    <Centered icon={<Loader2 className="size-5 animate-spin text-primary" aria-hidden="true" />} title={queued ? 'Waiting for the analysis to start' : 'Mapping the architecture'}>
      <p role="status">
        {queued
          ? 'The analysis is queued. It runs in the background and this page updates by itself when it is done.'
          : status?.status === 'running'
            ? 'Reading imports and building the module graph. This usually takes a few seconds.'
            : 'Requesting an analysis of the latest ingested commit…'}
      </p>
      {queued && age > SLOW_QUEUE_MS && (
        <p className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning">
          Still queued after {Math.round(age / 1000)} seconds. If it stays that way, check that the background worker is running
          (<code className="font-mono">python -m apps.api.worker</code>).
        </p>
      )}
    </Centered>
  )
}

export function FailedState({ status, onRetry, retrying }: { status: AnalysisStatus; onRetry: () => void; retrying: boolean }) {
  return (
    <Centered icon={<AlertCircle className="size-5 text-destructive" aria-hidden="true" />} title="The analysis failed">
      <p role="alert">{status.error ?? 'Something went wrong while analyzing this repository.'}</p>
      <Button size="sm" onClick={onRetry} disabled={retrying}>
        <RefreshCw className={cn(retrying && 'animate-spin')} aria-hidden="true" />
        Try again
      </Button>
    </Centered>
  )
}

export function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <Centered icon={<AlertCircle className="size-5 text-destructive" aria-hidden="true" />} title="Could not load the architecture">
      <p role="alert">{message}</p>
      <Button size="sm" variant="outline" onClick={onRetry}>
        <RefreshCw aria-hidden="true" />
        Retry
      </Button>
    </Centered>
  )
}

export function EmptyGraphState({ warnings }: { warnings: string[] }) {
  return (
    <Centered icon={<Network className="size-5 text-muted-foreground" aria-hidden="true" />} title="No modules to show">
      <p>The analysis did not find any Python, JavaScript, TypeScript or TSX source files in this snapshot, so there is nothing to map.</p>
      {warnings.length > 0 && (
        <ul className="text-xs">
          {warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
    </Centered>
  )
}
