'use client'

import Link from 'next/link'
import { ArrowRight, Code2, FileCode2, FolderTree, MessageSquareText, Network, Scale, type LucideIcon } from 'lucide-react'
import { languageColor } from '@/components/language-dot'
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
  const actions = [
    { href: `/repos/${repo.id}?tab=code`, label: 'Explore code', hint: 'Browse files and source', icon: Code2 },
    { href: `/repos/${repo.id}?tab=ask`, label: 'Ask AI', hint: 'Questions with file citations', icon: MessageSquareText },
    { href: `/repos/${repo.id}/graph`, label: 'Architecture', hint: 'Module dependencies', icon: Network },
  ]

  return (
    <div className="h-full overflow-y-auto">
      <div className="flex w-full max-w-5xl flex-col gap-8 px-4 py-6 sm:px-6">
        <section className="flex flex-col gap-2">
          {repo.description && (
            <p className="max-w-3xl text-pretty leading-relaxed text-muted-foreground">{repo.description}</p>
          )}
          <p className="text-xs text-muted-foreground">
            Last ingested {formatRelativeTime(repo.lastAnalyzedAt)}
            {repo.license && (
              <>
                {' · '}
                <span className="inline-flex items-center gap-1">
                  <Scale className="size-3" aria-hidden="true" />
                  {repo.license}
                </span>
              </>
            )}
          </p>
        </section>

        {repo.stats && (
          <section aria-label="Statistics" className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Stat icon={FileCode2} label="Files" value={formatNumber(repo.stats.files)} />
            <Stat icon={FolderTree} label="Directories" value={formatNumber(repo.stats.directories)} />
            <Stat icon={Code2} label="Lines of code" value={formatNumber(repo.stats.linesOfCode)} />
          </section>
        )}

        {repo.languages.length > 0 && (
          <section className="flex flex-col gap-3" aria-label="Languages">
            <h2 className="text-sm font-medium">Languages</h2>
            <div className="flex h-2 overflow-hidden rounded-full bg-muted" role="img" aria-label="Language breakdown">
              {repo.languages.map((l) => (
                <span key={l.name} className={cn('h-full', languageColor(l.name))} style={{ width: `${l.percent}%` }} />
              ))}
            </div>
            <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              {repo.languages.map((l) => (
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
              <span className="flex size-8 items-center justify-center rounded-md bg-muted">
                <Icon className="size-4" aria-hidden="true" />
              </span>
              <span className="flex-1">
                <span className="block text-sm font-medium">{label}</span>
                <span className="block text-xs text-muted-foreground">{hint}</span>
              </span>
              <ArrowRight className="size-4 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" aria-hidden="true" />
            </Link>
          ))}
        </nav>

        <section className="flex flex-col gap-2 rounded-lg border border-dashed border-border p-4" aria-label="Architecture summary">
          <h2 className="text-sm font-medium">Architecture summary</h2>
          <p className="text-sm text-muted-foreground">
            Coming soon. Entry points, key modules and an architecture summary will appear here once analysis is
            available.
          </p>
        </section>
      </div>
    </div>
  )
}
