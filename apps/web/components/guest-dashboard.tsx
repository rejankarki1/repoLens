'use client'

import Link from 'next/link'
import { ArrowRight } from 'lucide-react'
import { DemoRepositoryCard } from '@/components/demo-repository-card'
import { GitHubSignIn } from '@/components/auth/github-sign-in'
import { PageShell, SectionHeading } from '@/components/page-shell'
import { RepositoryImportForm } from '@/components/repository-import-form'
import { Skeleton } from '@/components/ui/skeleton'
import { useRepositories } from '@/lib/api/hooks'

/**
 * What a visitor sees on `/dashboard`: the two demo repositories, front and center, with their own Ask AI /
 * Explore Code / Architecture actions; importing a repository of your own is a secondary action below it,
 * with sign-in offered up front rather than only after a paste fails.
 */
export function GuestDashboard() {
  const { data, isLoading } = useRepositories()
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
          <ul className="grid gap-4 sm:grid-cols-2" aria-busy="true" aria-label="Loading demo repositories">
            {Array.from({ length: 2 }, (_, i) => (
              <Skeleton key={i} className="h-52 rounded-lg" />
            ))}
          </ul>
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
        <SectionHeading id="import-heading" action={<GitHubSignIn next="/dashboard" size="sm" />}>
          Import your own repository
        </SectionHeading>
        <p className="max-w-2xl text-sm text-muted-foreground">
          Sign in with GitHub to analyze any public repository, ask your own questions without the daily guest limit, and keep a
          private workspace. We only read your public profile.
        </p>
        <RepositoryImportForm />
      </section>

      <Link href="/#how-it-works" className="inline-flex w-fit items-center gap-1.5 text-sm font-medium text-primary hover:underline">
        How RepoLens works
        <ArrowRight className="size-3.5" aria-hidden="true" />
      </Link>
    </PageShell>
  )
}
