'use client'

import { GuestDashboard } from '@/components/guest-dashboard'
import { MemberDashboard } from '@/components/member-dashboard'
import { PageShell } from '@/components/page-shell'
import { Skeleton } from '@/components/ui/skeleton'
import { useAuth } from '@/lib/auth/use-auth'

/** `/dashboard` is two different screens: a demo workspace for visitors, an import-and-recents workspace for
 * members. Picking between them needs to know who is asking, so this is a client component; `page.tsx` keeps
 * the route's metadata. */
export function DashboardView() {
  const { authenticated, loading } = useAuth()

  if (loading) {
    return (
      <PageShell className="gap-10">
        <Skeleton className="h-8 w-64" />
        <div className="grid gap-4 sm:grid-cols-2">
          <Skeleton className="h-52 rounded-lg" />
          <Skeleton className="h-52 rounded-lg" />
        </div>
      </PageShell>
    )
  }

  return authenticated ? <MemberDashboard /> : <GuestDashboard />
}
