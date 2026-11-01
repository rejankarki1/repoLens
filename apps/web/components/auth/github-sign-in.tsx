'use client'

import { GitHubIcon } from '@/components/icons'
import { Button } from '@/components/ui/button'
import { loginUrl } from '@/lib/auth/session'
import { useAuth } from '@/lib/auth/use-auth'

/** "Continue with GitHub": a plain link, so sign-in is a normal browser navigation (the API controls the whole flow). */
export function GitHubSignIn({
  next,
  size = 'default',
  variant = 'default',
  label = 'Continue with GitHub',
  className,
}: {
  /** Site-relative path to return to after signing in. */
  next: string
  size?: 'default' | 'sm' | 'lg'
  variant?: 'default' | 'outline'
  label?: string
  className?: string
}) {
  const { loginEnabled } = useAuth()
  if (!loginEnabled) {
    return (
      <p role="status" className="text-xs text-muted-foreground">
        GitHub sign-in is not configured on this server.
      </p>
    )
  }
  return (
    <Button size={size} variant={variant} className={className} render={<a href={loginUrl(next)} />} nativeButton={false}>
      <GitHubIcon aria-hidden="true" />
      {label}
    </Button>
  )
}
