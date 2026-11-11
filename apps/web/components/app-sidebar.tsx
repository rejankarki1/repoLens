'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useState } from 'react'
import { AlertCircle, FolderGit2, LayoutDashboard, Loader2, Menu } from 'lucide-react'
import { RepoLensLogo } from '@/components/icons'
import { UserMenu } from '@/components/auth/user-menu'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from '@/components/ui/sheet'
import { useRepositories } from '@/lib/api/hooks'
import { useAuth } from '@/lib/auth/use-auth'
import { sortByRecent } from '@/lib/recent-repos'
import { useRecentlyOpened } from '@/lib/use-recently-opened'
import { cn } from '@/lib/utils'

const NAV_ITEMS = [
  { href: '/dashboard', label: 'Dashboard', icon: LayoutDashboard, match: (p: string) => p === '/dashboard' },
  { href: '/repositories', label: 'Repositories', icon: FolderGit2, match: (p: string) => p.startsWith('/repositories') },
]

const LINK =
  'flex h-8 items-center gap-2.5 rounded-md px-2.5 text-sm transition-colors focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/40'
const ACTIVE = 'bg-sidebar-accent font-medium text-sidebar-accent-foreground'
const IDLE = 'text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground'

/** The newest repositories, from the list the other screens already load (no extra request). A visitor sees the demo set. */
function RecentRepositories({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname()
  const { authenticated, loading } = useAuth()
  const { data } = useRepositories()
  const opened = useRecentlyOpened()
  const repositories = sortByRecent(data ?? [], opened).slice(0, 5)
  if (loading || repositories.length === 0) return null

  return (
    <nav aria-label={authenticated ? 'Recent repositories' : 'Demo repositories'} className="flex flex-col gap-0.5 px-2 pb-2">
      <p className="px-2.5 pb-1 text-xs font-medium text-muted-foreground">{authenticated ? 'Recent' : 'Demo'}</p>
      {repositories.map((repo) => {
        const active = pathname === `/repos/${repo.id}` || pathname.startsWith(`/repos/${repo.id}/`)
        const working = repo.status === 'queued' || repo.status === 'importing' || repo.status === 'indexing'
        return (
          <Link
            key={repo.id}
            href={`/repos/${repo.id}`}
            title={repo.fullName}
            onClick={onNavigate}
            aria-current={active ? 'page' : undefined}
            className={cn(LINK, 'h-7 text-[0.8125rem]', active ? ACTIVE : IDLE)}
          >
            <span className="min-w-0 flex-1 truncate">{repo.name}</span>
            {working && <Loader2 className="size-3.5 shrink-0 animate-spin text-warning" aria-label="Ingesting" />}
            {repo.status === 'failed' && <AlertCircle className="size-3.5 shrink-0 text-destructive" aria-label="Ingestion failed" />}
          </Link>
        )
      })}
    </nav>
  )
}

function SidebarContents({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname()

  return (
    <div className="flex h-full flex-col">
      <Link href="/" onClick={onNavigate} className="flex h-14 items-center gap-2.5 px-4 focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/40">
        <RepoLensLogo className="size-6" />
        <span className="text-sm font-semibold tracking-tight">RepoLens</span>
      </Link>

      <nav aria-label="Main" className="flex flex-col gap-0.5 px-2 py-2">
        {NAV_ITEMS.map(({ href, label, icon: Icon, match }) => {
          const active = match(pathname)
          return (
            <Link key={href} href={href} onClick={onNavigate} aria-current={active ? 'page' : undefined} className={cn(LINK, active ? ACTIVE : IDLE)}>
              <Icon className="size-4" aria-hidden="true" />
              {label}
            </Link>
          )
        })}
      </nav>

      <div className="min-h-0 flex-1 overflow-y-auto pt-2">
        <RecentRepositories onNavigate={onNavigate} />
      </div>

      <UserMenu />
    </div>
  )
}

export function AppSidebar() {
  return (
    <aside className="hidden w-60 shrink-0 border-r border-sidebar-border bg-sidebar text-sidebar-foreground md:block">
      <SidebarContents />
    </aside>
  )
}

export function MobileTopBar() {
  const [open, setOpen] = useState(false)
  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border bg-sidebar px-3 md:hidden">
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetTrigger render={<Button variant="ghost" size="icon-sm" aria-label="Open navigation" />}>
          <Menu />
        </SheetTrigger>
        <SheetContent side="left" className="w-64 bg-sidebar p-0">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <SidebarContents onNavigate={() => setOpen(false)} />
        </SheetContent>
      </Sheet>
      <RepoLensLogo className="size-5" />
      <span className="text-sm font-semibold">RepoLens</span>
    </header>
  )
}
