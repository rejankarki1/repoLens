'use client'

import { DemoRepositoryCard } from '@/components/demo-repository-card'
import { Skeleton } from '@/components/ui/skeleton'
import { useGuestAsk, useRepositories } from '@/lib/api/hooks'
import { useAuth } from '@/lib/auth/use-auth'

/** The demo repositories the server publishes (the guest showcase), read from the API. Hidden when there are none. */
export function DemoRepositories() {
  const { data, isLoading } = useRepositories()
  const { authenticated } = useAuth()
  const first = data?.[0]
  const { data: guestAsk } = useGuestAsk(first?.id ?? '', !!first)
  if (authenticated || (!isLoading && (!data || data.length === 0))) return null

  return (
    <section id="demo" aria-labelledby="demo-heading" className="flex scroll-mt-8 flex-col gap-5">
      <div className="flex flex-col gap-1.5">
        <h2 id="demo-heading" className="text-2xl font-semibold tracking-tight">
          Try it on a real repository
        </h2>
        <p className="max-w-2xl text-muted-foreground">
          {guestAsk?.enabled
            ? `Both repositories are already indexed. Without signing in, you can ask up to ${guestAsk.limit} real questions a day about them, answered from the code with cited sources. Example answers, the code and the architecture are always open.`
            : 'Both repositories are already indexed. Browse the code and the architecture, and read example questions with their cited sources. No account needed.'}
        </p>
      </div>
      <ul className="grid gap-4 sm:grid-cols-2">
        {isLoading ? Array.from({ length: 2 }, (_, i) => <Skeleton key={i} className="h-52 rounded-lg" />) : data!.slice(0, 2).map((repo) => <DemoRepositoryCard key={repo.id} repo={repo} />)}
      </ul>
    </section>
  )
}
