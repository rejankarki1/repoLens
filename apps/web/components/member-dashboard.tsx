'use client'

import Link from 'next/link'
import { ArrowRight } from 'lucide-react'
import { PageShell, SectionHeading } from '@/components/page-shell'
import { RepositoryImportForm } from '@/components/repository-import-form'
import { RepositoryList } from '@/components/repository-list'
import { StatusGuide } from '@/components/status-guide'

/** The signed-in dashboard: import, then the repositories you actually opened recently, in that order. */
export function MemberDashboard() {
  return (
    <PageShell className="gap-10">
      <section aria-labelledby="import-heading" className="flex flex-col gap-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex min-w-0 flex-col gap-1">
            <h1 id="import-heading" className="text-2xl font-semibold tracking-tight">
              Analyze a repository
            </h1>
            <p className="max-w-3xl text-sm text-muted-foreground">
              Paste a public GitHub URL. RepoLens indexes it so you can explore the code, see its architecture and ask questions with cited sources.
            </p>
          </div>
          <Link
            href="/dashboard?view=demo"
            className="inline-flex shrink-0 items-center gap-1.5 rounded text-sm font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/40"
          >
            Browse the demo repositories
            <ArrowRight className="size-3.5" aria-hidden="true" />
          </Link>
        </div>
        <RepositoryImportForm />
      </section>

      <section aria-labelledby="recent-heading" className="flex flex-col gap-3">
        <SectionHeading
          id="recent-heading"
          action={
            <Link
              href="/repositories"
              className="rounded text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/40"
            >
              All repositories
            </Link>
          }
        >
          Recent repositories
        </SectionHeading>
        <RepositoryList limit={6} />
      </section>

      <StatusGuide />
    </PageShell>
  )
}
