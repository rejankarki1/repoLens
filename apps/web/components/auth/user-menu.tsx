'use client'

import Image from 'next/image'
import { usePathname, useSearchParams } from 'next/navigation'
import { ChevronsUpDown, ExternalLink, LogOut, Moon, Sun } from 'lucide-react'
import { useTheme } from 'next-themes'
import { Suspense, useEffect, useState } from 'react'
import { GitHubSignIn } from '@/components/auth/github-sign-in'
import { ThemeToggle } from '@/components/theme-toggle'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { authErrorMessage } from '@/lib/auth/import-resume'
import { useAuth } from '@/lib/auth/use-auth'
import { clearRecentlyOpened } from '@/lib/use-recently-opened'

/** A failed or cancelled sign-in can return to any page (`next` is wherever the visitor started from), not just
 * `/dashboard`. `/dashboard` already shows this itself (next to the import box, with its own query-string cleanup),
 * so this only covers every other page. */
function GuestSignInError() {
  const pathname = usePathname()
  const params = useSearchParams().toString()
  const [message] = useState(() => (pathname === '/dashboard' ? null : authErrorMessage(params)))

  useEffect(() => {
    if (message) window.history.replaceState(null, '', window.location.pathname)
  }, [message])

  if (!message) return null
  return (
    <p role="status" className="text-xs text-destructive">
      {message}
    </p>
  )
}

/** Bottom of the sidebar: a compact profile menu (profile, theme, sign out) or, for a visitor, Sign in with GitHub. */
export function UserMenu() {
  const { user, authenticated, loading, signOut } = useAuth()
  const { resolvedTheme, setTheme } = useTheme()
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
        <Suspense fallback={null}>
          <GuestSignInError />
        </Suspense>
        <p className="text-xs text-muted-foreground">Browsing the demo. Sign in to analyze your own repositories.</p>
        <div className="flex items-center gap-2">
          <GitHubSignIn next={pathname === '/' ? '/dashboard' : pathname} size="sm" className="flex-1" />
          <ThemeToggle />
        </div>
      </div>
    )
  }

  const display = user.name || user.login
  return (
    <div className="border-t border-sidebar-border p-2">
      <DropdownMenu>
        <DropdownMenuTrigger
          aria-label={`Account menu for ${display}`}
          className="flex w-full items-center gap-2.5 rounded-md p-1.5 text-left outline-none transition-colors hover:bg-sidebar-accent/60 focus-visible:ring-3 focus-visible:ring-ring/40 aria-expanded:bg-sidebar-accent/60"
        >
          {user.avatar_url ? (
            <Image src={user.avatar_url} alt="" width={28} height={28} unoptimized className="size-7 shrink-0 rounded-full bg-muted" />
          ) : (
            <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/15 text-xs font-semibold text-primary" aria-hidden="true">
              {user.login.slice(0, 2).toUpperCase()}
            </span>
          )}
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-medium leading-5" title={display}>
              {display}
            </span>
            <span className="block truncate text-xs leading-4 text-muted-foreground">@{user.login}</span>
          </span>
          <ChevronsUpDown className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        </DropdownMenuTrigger>
        <DropdownMenuContent side="top" align="start" className="min-w-52">
          <DropdownMenuGroup>
            <DropdownMenuLabel>Signed in as @{user.login}</DropdownMenuLabel>
            <DropdownMenuItem render={<a href={`https://github.com/${encodeURIComponent(user.login)}`} target="_blank" rel="noreferrer" />}>
              <ExternalLink aria-hidden="true" />
              GitHub profile
            </DropdownMenuItem>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuLabel>Theme</DropdownMenuLabel>
            <DropdownMenuRadioGroup value={resolvedTheme ?? 'dark'} onValueChange={(value) => setTheme(String(value))}>
              <DropdownMenuRadioItem value="light">
                <Sun aria-hidden="true" />
                Light
              </DropdownMenuRadioItem>
              <DropdownMenuRadioItem value="dark">
                <Moon aria-hidden="true" />
                Dark
              </DropdownMenuRadioItem>
            </DropdownMenuRadioGroup>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            disabled={leaving}
            onClick={async () => {
              setLeaving(true)
              clearRecentlyOpened()
              await signOut()
              setLeaving(false)
            }}
          >
            <LogOut aria-hidden="true" />
            {leaving ? 'Signing out…' : 'Sign out'}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}
