'use client'

import Image from 'next/image'
import { usePathname } from 'next/navigation'
import { Loader2, LogOut } from 'lucide-react'
import { useState } from 'react'
import { GitHubSignIn } from '@/components/auth/github-sign-in'
import { ThemeToggle } from '@/components/theme-toggle'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/lib/auth/use-auth'

/** Bottom of the sidebar: who is signed in with a sign-out action, or Continue with GitHub for a visitor. */
export function UserMenu() {
  const { user, authenticated, loading, signOut } = useAuth()
  const pathname = usePathname()
  const [leaving, setLeaving] = useState(false)

  if (loading) {
    return (
      <div className="flex h-14 items-center gap-2.5 border-t border-sidebar-border p-3" aria-busy="true">
        <div className="size-8 shrink-0 animate-pulse rounded-full bg-muted" />
        <div className="h-4 w-24 animate-pulse rounded bg-muted" />
      </div>
    )
  }

  if (!authenticated || !user) {
    return (
      <div className="flex flex-col gap-2 border-t border-sidebar-border p-3">
        <p className="text-xs text-muted-foreground">Browsing as a guest. Sign in to analyze repositories and ask questions.</p>
        <div className="flex items-center gap-2">
          <GitHubSignIn next={pathname === '/' ? '/repositories' : pathname} size="sm" className="flex-1" />
          <ThemeToggle />
        </div>
      </div>
    )
  }

  const display = user.name || user.login
  return (
    <div className="flex items-center gap-3 border-t border-sidebar-border px-3 py-3">
      {user.avatar_url ? (
        <Image src={user.avatar_url} alt="" width={32} height={32} unoptimized className="size-8 shrink-0 rounded-full bg-muted" />
      ) : (
        <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/15 text-xs font-semibold text-primary" aria-hidden="true">
          {user.login.slice(0, 2).toUpperCase()}
        </div>
      )}
      <div className="min-w-0 flex-1 overflow-hidden">
        <p className="truncate text-sm font-medium leading-5" title={display}>{display}</p>
        <p className="truncate text-xs leading-4 text-muted-foreground">@{user.login}</p>
      </div>
      <Button
        variant="ghost"
        size="icon-sm"
        className="shrink-0"
        aria-label="Sign out"
        title="Sign out"
        disabled={leaving}
        onClick={async () => {
          setLeaving(true)
          await signOut()
          setLeaving(false)
        }}
      >
        {leaving ? <Loader2 className="animate-spin" aria-hidden="true" /> : <LogOut aria-hidden="true" />}
      </Button>
      <ThemeToggle />
    </div>
  )
}
