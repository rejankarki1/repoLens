'use client'

import { Suspense } from 'react'
import { useSearchParams } from 'next/navigation'
import { GuestDashboard } from '@/components/guest-dashboard'
import { MemberDashboard } from '@/components/member-dashboard'
import { PageShell } from '@/components/page-shell'
import { SlowStartHint } from '@/components/slow-start-hint'
import { Skeleton } from '@/components/ui/skeleton'
import { useRepositories } from '@/lib/api/hooks'
import { useAuth } from '@/lib/auth/use-auth'

function DashboardSkeleton() {
  return (
    <PageShell className="gap-10">
      <Skeleton className="h-8 w-64" />
      <div className="grid gap-4 sm:grid-cols-2">
        <Skeleton className="h-52 rounded-lg" />
        <Skeleton className="h-52 rounded-lg" />
      </div>
      <SlowStartHint />
    </PageShell>
  )
}

/**
 * `/dashboard` is two different screens: the demo workspace (`GuestDashboard`) and a member's own
 * import-and-recents workspace (`MemberDashboard`). The demo one is shown for a visitor, for `?view=demo`
 * (how "Try Demo" reaches the demo even when already signed in), and for a signed-in member who has not
 * imported anything yet — a brand-new workspace should never look emptier than the demo visitors get.
 * Picking between them needs to know who is asking, so this is a client component; `page.tsx` keeps the
 * route's metadata. `useSearchParams` needs a Suspense boundary, same as `RepositoryImportForm`.
 */
export function DashboardView() {
  return (
    <Suspense fallback={<DashboardSkeleton />}>
      <DashboardRouter />
    </Suspense>
  )
}

function DashboardRouter() {
  const { authenticated, loading } = useAuth()
  const demoRequested = useSearchParams().get('view') === 'demo'
  const { data: repositories, isLoading: reposLoading } = useRepositories()

  if (loading) return <DashboardSkeleton />
  if (!authenticated || demoRequested) return <GuestDashboard />
  if (reposLoading) return <DashboardSkeleton />
  return (repositories?.length ?? 0) > 0 ? <MemberDashboard /> : <GuestDashboard />
}
