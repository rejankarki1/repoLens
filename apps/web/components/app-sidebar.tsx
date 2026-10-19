'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { FolderGit2, LayoutDashboard, Menu, Settings } from 'lucide-react'
import { RepoLensLogo } from '@/components/icons'
import { ThemeToggle } from '@/components/theme-toggle'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'

const NAV_ITEMS = [
  { href: '/dashboard', label: 'Dashboard', icon: LayoutDashboard, match: (p: string) => p === '/dashboard' },
  {
    href: '/repositories',
    label: 'Repositories',
    icon: FolderGit2,
    match: (p: string) => p.startsWith('/repositories') || p.startsWith('/repos/'),
  },
  { href: '/settings', label: 'Settings', icon: Settings, match: (p: string) => p.startsWith('/settings') },
]

function SidebarContents() {
  const pathname = usePathname()

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-14 items-center gap-2.5 px-4">
        <RepoLensLogo className="size-6" />
        <span className="text-sm font-semibold tracking-tight">RepoLens</span>
        <span className="ml-auto rounded border border-border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
          Demo
        </span>
      </div>

      <nav aria-label="Main" className="flex flex-1 flex-col gap-0.5 px-2 py-2">
        {NAV_ITEMS.map(({ href, label, icon: Icon, match }) => {
          const active = match(pathname)
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? 'page' : undefined}
              className={cn(
                'flex h-8 items-center gap-2.5 rounded-md px-2.5 text-sm transition-colors',
                active
                  ? 'bg-sidebar-accent font-medium text-sidebar-accent-foreground'
                  : 'text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground',
              )}
            >
              <Icon className="size-4" aria-hidden="true" />
              {label}
            </Link>
          )
        })}
      </nav>

      <div className="flex items-center gap-2.5 border-t border-sidebar-border p-3">
        <div
          className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/15 text-xs font-semibold text-primary"
          aria-hidden="true"
        >
          AD
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">Alex Doe</p>
          <p className="truncate text-xs text-muted-foreground">Demo workspace</p>
        </div>
        <ThemeToggle />
      </div>
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
  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border bg-sidebar px-3 md:hidden">
      <Sheet>
        <SheetTrigger render={<Button variant="ghost" size="icon-sm" aria-label="Open navigation" />}>
          <Menu />
        </SheetTrigger>
        <SheetContent side="left" className="w-64 bg-sidebar p-0">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <SidebarContents />
        </SheetContent>
      </Sheet>
      <RepoLensLogo className="size-5" />
      <span className="text-sm font-semibold">RepoLens</span>
    </header>
  )
}
