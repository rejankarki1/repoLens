'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useEffect } from 'react'
import { ArrowRight, LayoutDashboard } from 'lucide-react'
import { GitHubSignIn } from '@/components/auth/github-sign-in'
import { Button, buttonVariants } from '@/components/ui/button'
import { useGuestAsk, useRepositories } from '@/lib/api/hooks'
import { useAuth } from '@/lib/auth/use-auth'
import { cn } from '@/lib/utils'

/** Signed-in users have no use for the landing page: send them to their dashboard as soon as the session is known. */
export function AuthRedirect() {
  const router = useRouter()
  const { authenticated } = useAuth()
  useEffect(() => {
    if (authenticated) router.replace('/dashboard')
  }, [authenticated, router])
  return null
}

/**
 * Where "Try Demo" goes: the first demo (showcase) repository's example questions, or the list of demos while it is
 * loading. Returns null once the server is known to publish none, so the page never promises a demo that is not there.
 */
export function TryDemoButton({ size = 'lg' }: { size?: 'default' | 'lg' }) {
  const { data, isLoading } = useRepositories()
  // The list is newest first; the first demo is the one imported first.
  const first = data?.[data.length - 1]
  if (!isLoading && !first) return null
  return (
    <Link href={first ? `/repos/${first.id}?tab=ask` : '/repositories'} className={cn(buttonVariants({ size }))}>
      Try Demo
      <ArrowRight aria-hidden="true" />
    </Link>
  )
}

export function HeroActions() {
  const { authenticated, loading } = useAuth()
  if (authenticated) {
    return (
      <Link href="/dashboard" className={cn(buttonVariants({ size: 'lg' }))}>
        <LayoutDashboard aria-hidden="true" />
        Open dashboard
      </Link>
    )
  }
  return (
    <>
      <TryDemoButton />
      {!loading && <GitHubSignIn next="/dashboard" size="lg" variant="outline" label="Sign in with GitHub" />}
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
  const { data } = useRepositories()
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
