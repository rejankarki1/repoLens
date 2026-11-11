import type { Metadata } from 'next'
import Link from 'next/link'
import { PageShell, SectionHeading } from '@/components/page-shell'
import { RepositoryImportForm } from '@/components/repository-import-form'
import { RepositoryList } from '@/components/repository-list'

export const metadata: Metadata = { title: 'Dashboard' }

export default function DashboardPage() {
  return (
    <PageShell className="gap-10">
      <section aria-labelledby="import-heading" className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <h1 id="import-heading" className="text-2xl font-semibold tracking-tight">
            Analyze a repository
          </h1>
          <p className="max-w-2xl text-sm text-muted-foreground">
            Paste a public GitHub URL to explore its code and architecture and ask questions that cite the exact lines.
          </p>
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
              Manage repositories
            </Link>
          }
        >
          Recent repositories
        </SectionHeading>
        <RepositoryList limit={6} />
      </section>
    </PageShell>
  )
}
