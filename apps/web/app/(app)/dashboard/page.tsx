import type { Metadata } from 'next'
import Link from 'next/link'
import { RepositoryImportForm } from '@/components/repository-import-form'
import { RepositoryList } from '@/components/repository-list'

export const metadata: Metadata = { title: 'Dashboard' }

export default function DashboardPage() {
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-12 px-4 py-10 sm:px-8 md:py-16">
      <section aria-labelledby="welcome-heading" className="flex flex-col gap-6">
        <div className="flex flex-col gap-3">
          <h1 id="welcome-heading" className="text-balance text-3xl font-semibold tracking-tight md:text-4xl">
            Understand any codebase.
          </h1>
          <p className="max-w-2xl text-pretty leading-relaxed text-muted-foreground">
            Import a GitHub repository to browse its source, map how modules depend on each other, and ask questions
            answered with citations to the exact files and lines.
          </p>
        </div>
        <RepositoryImportForm />
      </section>

      <section aria-labelledby="recent-heading" className="flex flex-col gap-4">
        <div className="flex items-baseline justify-between">
          <h2 id="recent-heading" className="text-sm font-medium">
            Recent repositories
          </h2>
          <Link href="/repositories" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
            View all
          </Link>
        </div>
        <RepositoryList limit={6} />
      </section>
    </div>
  )
}
