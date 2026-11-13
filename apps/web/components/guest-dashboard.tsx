'use client'

import Link from 'next/link'
import { ArrowRight } from 'lucide-react'
import { DemoRepositoryCard } from '@/components/demo-repository-card'
import { PageShell, SectionHeading } from '@/components/page-shell'
import { RepositoryImportForm } from '@/components/repository-import-form'
import { SlowStartHint } from '@/components/slow-start-hint'
import { Skeleton } from '@/components/ui/skeleton'
import { useShowcaseRepositories } from '@/lib/api/hooks'

/**
 * The demo dashboard: the two demo repositories, front and center, with their own Ask AI / Explore Code /
 * Architecture actions; importing a repository of your own is a secondary action below it. This is what
 * `/dashboard` shows to a visitor, and also what a signed-in member sees for a brand-new, empty workspace or
 * after asking for `?view=demo` — always the same showcase list, never a member's own repositories. Sign-in
 * is not pitched up front — `RepositoryImportForm` offers it contextually, once a guest pastes a URL, so
 * there is never more than one sign-in prompt on screen at a time.
 */
export function GuestDashboard() {
  const { data, isLoading } = useShowcaseRepositories()
  const repositories = (data ?? []).slice(0, 2)

  return (
    <PageShell className="gap-10">
      <section aria-labelledby="demo-heading" className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <h1 id="demo-heading" className="text-2xl font-semibold tracking-tight">
            Demo repositories
          </h1>
          <p className="max-w-2xl text-sm text-muted-foreground">
            Two real, already-indexed repositories. Ask AI, browse the code, and see how the modules connect, no account needed.
          </p>
        </div>
        {isLoading ? (
          <>
            <ul className="grid gap-4 sm:grid-cols-2" aria-busy="true" aria-label="Loading demo repositories">
              {Array.from({ length: 2 }, (_, i) => (
                <Skeleton key={i} className="h-52 rounded-lg" />
              ))}
            </ul>
            <SlowStartHint />
          </>
        ) : repositories.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border px-6 py-10 text-center text-sm text-muted-foreground">
            This server has not published any demo repositories yet.
          </p>
        ) : (
          <ul className="grid gap-4 sm:grid-cols-2">
            {repositories.map((repo) => (
              <DemoRepositoryCard key={repo.id} repo={repo} />
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="import-heading" className="flex flex-col gap-3 rounded-lg border border-dashed border-border p-5">
        <SectionHeading id="import-heading">Import your own repository</SectionHeading>
        <p className="max-w-2xl text-sm text-muted-foreground">Paste a public GitHub URL to analyze it.</p>
        <RepositoryImportForm />
      </section>

      <Link href="/#how-it-works" className="inline-flex w-fit items-center gap-1.5 text-sm font-medium text-primary hover:underline">
        How RepoLens works
        <ArrowRight className="size-3.5" aria-hidden="true" />
      </Link>
    </PageShell>
  )
}
