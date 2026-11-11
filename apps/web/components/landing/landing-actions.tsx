'use client'

import Link from 'next/link'
import { ArrowRight, LayoutDashboard } from 'lucide-react'
import { GitHubSignIn } from '@/components/auth/github-sign-in'
import { Button, buttonVariants } from '@/components/ui/button'
import { useGuestAsk, useShowcaseRepositories } from '@/lib/api/hooks'
import { useAuth } from '@/lib/auth/use-auth'
import { cn } from '@/lib/utils'

/**
 * "Try Demo" always opens the demo dashboard (`/dashboard?view=demo`), with both showcase repositories, even
 * for a signed-in visitor who would otherwise land on their own workspace. Returns null once the server is
 * known to publish no demo repositories, so the page never promises a demo that is not there.
 */
export function TryDemoButton({ size = 'lg' }: { size?: 'default' | 'lg' }) {
  const { data, isLoading } = useShowcaseRepositories()
  if (!isLoading && data?.length === 0) return null
  return (
    <Link href="/dashboard?view=demo" className={cn(buttonVariants({ size }))}>
      Try Demo
      <ArrowRight aria-hidden="true" />
    </Link>
  )
}

export function HeroActions() {
  const { authenticated, loading } = useAuth()
  return (
    <>
      {authenticated && (
        <Link href="/dashboard" className={cn(buttonVariants({ size: 'lg' }))}>
          <LayoutDashboard aria-hidden="true" />
          Open dashboard
        </Link>
      )}
      <TryDemoButton />
      {!authenticated && !loading && <GitHubSignIn next="/dashboard" size="lg" variant="outline" label="Sign in with GitHub" />}
    </>
  )
}

export function HeaderAction() {
  const { authenticated, loading } = useAuth()
  if (loading) return <Button size="sm" variant="outline" disabled aria-busy="true" className="w-36" />
  return authenticated ? (
    <Link href="/dashboard" className={cn(buttonVariants({ size: 'sm', variant: 'outline' }))}>
      Open dashboard
    </Link>
  ) : (
    <GitHubSignIn next="/dashboard" size="sm" variant="outline" label="Sign in with GitHub" />
  )
}

/** The line under the hero buttons. It promises live questions only when the server has them switched on. */
export function HeroNote() {
  const { data } = useShowcaseRepositories()
  const first = data?.[0]
  const { data: guestAsk } = useGuestAsk(first?.id ?? '', !!first)
  return (
    <p className="max-w-xl text-xs text-muted-foreground">
      {guestAsk?.enabled
        ? `No account needed: ask up to ${guestAsk.limit} real questions a day about the demo repositories. Signing in only reads your public GitHub profile.`
        : 'The demo needs no account. Signing in only reads your public GitHub profile.'}
    </p>
  )
}
