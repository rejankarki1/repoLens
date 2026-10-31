'use client'

import Link from 'next/link'
import { ArrowRight, Code2, FileCode2, FolderTree, Loader2, MessageSquareText, Network, TriangleAlert, type LucideIcon } from 'lucide-react'
import { languageColor } from '@/components/language-dot'
import { Skeleton } from '@/components/ui/skeleton'
import { useArchitectureGraph, useArchitectureStatus, useConversations, useRepositoryStats } from '@/lib/api/hooks'
import { count, shortSha } from '@/lib/architecture/model'
import { formatNumber, formatRelativeTime } from '@/lib/repo-utils'
import type { Repository } from '@/lib/types'
import { cn } from '@/lib/utils'

function Stat({ icon: Icon, label, value }: { icon: LucideIcon; label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-border bg-card p-4">
      <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Icon className="size-3.5" aria-hidden="true" />
        {label}
      </span>
      <span className="text-xl font-semibold tabular-nums tracking-tight">{value}</span>
    </div>
  )
}

export function RepositoryOverview({ repo }: { repo: Repository }) {
  const stats = useRepositoryStats(repo)
  const conversations = useConversations(repo.id)
  const saved = conversations.data?.length ?? 0

  const actions = [
    { href: `/repos/${repo.id}?tab=code`, label: 'Explore code', hint: stats.data ? `${count(stats.data.files, 'file')} to browse` : 'Browse files and source', icon: Code2 },
    {
      href: `/repos/${repo.id}?tab=ask`, label: 'Ask AI', icon: MessageSquareText,
      hint: saved > 0 ? `${count(saved, 'saved conversation')}` : 'Questions with file citations',
    },
    { href: `/repos/${repo.id}/graph`, label: 'Architecture', hint: 'How the modules depend on each other', icon: Network },
  ]

  return (
    <div className="h-full overflow-y-auto">
      <div className="flex w-full max-w-5xl flex-col gap-8 px-4 py-6 sm:px-6">
        <p className="text-xs text-muted-foreground">
          {repo.lastAnalyzedAt ? `Last ingested ${formatRelativeTime(repo.lastAnalyzedAt)}` : 'Not ingested yet'}
          {repo.indexedCommit && (
            <>
              {' · commit '}
              <code className="font-mono text-foreground">{repo.indexedCommit}</code>
            </>
          )}
          {' · branch '}
          <code className="font-mono text-foreground">{repo.defaultBranch}</code>
        </p>

        <section aria-label="Statistics" className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {stats.isLoading || !stats.data ? (
            stats.error ? (
              <p className="col-span-full text-sm text-destructive">Could not load the file statistics.</p>
            ) : (
              Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-[4.5rem]" />)
            )
          ) : (
            <>
              <Stat icon={FileCode2} label="Files" value={formatNumber(stats.data.files)} />
              <Stat icon={FolderTree} label="Directories" value={formatNumber(stats.data.directories)} />
              <Stat icon={Code2} label="Lines" value={formatNumber(stats.data.lines)} />
              <Stat icon={FileCode2} label="Languages" value={String(stats.data.languages.filter((l) => l.name !== 'Other').length)} />
            </>
          )}
        </section>

        {stats.data && stats.data.languages.length > 0 && (
          <section className="flex flex-col gap-3" aria-label="Languages">
            <h2 className="text-sm font-medium">Languages</h2>
            <div className="flex h-2 overflow-hidden rounded-full bg-muted" role="img" aria-label="Language breakdown by lines">
              {stats.data.languages.map((l) => (
                <span key={l.name} className={cn('h-full', languageColor(l.name))} style={{ width: `${l.percent}%` }} />
              ))}
            </div>
            <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              {stats.data.languages.map((l) => (
                <li key={l.name} className="inline-flex items-center gap-1.5">
                  <span className={cn('size-2 rounded-full', languageColor(l.name))} aria-hidden="true" />
                  <span className="text-foreground">{l.name}</span>
                  <span className="tabular-nums">{l.percent}%</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <nav aria-label="Workspace shortcuts" className="grid gap-3 sm:grid-cols-3">
          {actions.map(({ href, label, hint, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className="group flex items-center gap-3 rounded-lg border border-border bg-card p-4 transition-colors hover:border-primary/40 hover:bg-accent/40"
            >
              <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-muted">
                <Icon className="size-4" aria-hidden="true" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium">{label}</span>
                <span className="block truncate text-xs text-muted-foreground">{hint}</span>
              </span>
              <ArrowRight className="size-4 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" aria-hidden="true" />
            </Link>
          ))}
        </nav>

        <ArchitectureSummary repo={repo} />
      </div>
    </div>
  )
}

/** The architecture analysis at a glance; it only reads an existing result and never starts an analysis. */
function ArchitectureSummary({ repo }: { repo: Repository }) {
  const status = useArchitectureStatus(repo)
  const graph = useArchitectureGraph(repo, status.data)
  const s = status.data
  const result = graph.data?.graph
  const open = (
    <Link href={`/repos/${repo.id}/graph`} className="inline-flex items-center gap-1 text-sm text-primary hover:underline">
      Open the architecture map <ArrowRight className="size-3.5" aria-hidden="true" />
    </Link>
  )

  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border p-4" aria-label="Architecture summary">
      <h2 className="text-sm font-medium">Architecture</h2>
      {status.isLoading ? (
        <Skeleton className="h-16" />
      ) : status.error || !s ? (
        <p className="text-sm text-muted-foreground">The analysis status could not be loaded.</p>
      ) : result ? (
        <>
          {s.freshness === 'stale' && (
            <p className="flex items-start gap-1.5 text-xs text-warning">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              From commit {shortSha(s.analyzed_commit_sha)}; the repository is now at {shortSha(s.current_commit_sha)}.
              {s.status === 'queued' || s.status === 'running' ? ' An update is running.' : ' Open the map to refresh it.'}
            </p>
          )}
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-5">
            {(
              [
                { label: 'Modules', value: result.stats.modules },
                { label: 'Dependencies', value: result.stats.file_edges },
                { label: 'Packages', value: result.stats.external_packages },
                { label: 'Circular imports', value: result.stats.cycles, tone: 'text-destructive' },
                { label: 'Unresolved imports', value: result.stats.unresolved_imports, tone: 'text-warning' },
              ] as { label: string; value: number; tone?: string }[]
            ).map(({ label, value, tone }) => (
              <div key={label}>
                <dt className="text-xs text-muted-foreground">{label}</dt>
                <dd className={cn('text-lg font-semibold tabular-nums', value > 0 && tone)}>{value}</dd>
              </div>
            ))}
          </dl>
          {result.technologies.length > 0 && (
            <ul className="flex flex-wrap gap-1.5" aria-label="Technologies">
              {result.technologies.map((t) => (
                <li key={t.id} className="rounded-full border border-border bg-card px-2.5 py-0.5 text-xs" title={t.category}>
                  {t.name}
                </li>
              ))}
            </ul>
          )}
          {result.areas.length > 0 && (
            <p className="text-xs text-muted-foreground">
              Detected from the code: {result.areas.map((a) => a.label.toLowerCase()).join(', ')}.
            </p>
          )}
          {open}
        </>
      ) : s.status === 'queued' || s.status === 'running' ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
          <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          The architecture is being analyzed. This takes a few seconds.
        </p>
      ) : s.status === 'failed' ? (
        <>
          <p className="text-sm text-destructive">The last analysis failed{s.error ? `: ${s.error}` : '.'}</p>
          {open}
        </>
      ) : (
        <>
          <p className="text-sm text-muted-foreground">Not analyzed yet. Opening the map starts the analysis.</p>
          {open}
        </>
      )}
    </section>
  )
}
