'use client'

import Link from 'next/link'
import { usePathname, useSearchParams } from 'next/navigation'
import { Code2, LayoutGrid, MessageSquareText, Network } from 'lucide-react'
import { cn } from '@/lib/utils'

export type WorkspaceTab = 'overview' | 'code' | 'ask' | 'architecture'

export function parseTab(value: string | null): Exclude<WorkspaceTab, 'architecture'> {
  return value === 'code' || value === 'ask' ? value : 'overview'
}

export function WorkspaceNav({ id }: { id: string }) {
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const active: WorkspaceTab = pathname.endsWith('/graph') ? 'architecture' : parseTab(searchParams.get('tab'))

  const tabs = [
    { key: 'overview', label: 'Overview', icon: LayoutGrid, href: `/repos/${id}` },
    { key: 'code', label: 'Explore Code', icon: Code2, href: `/repos/${id}?tab=code` },
    { key: 'ask', label: 'Ask AI', icon: MessageSquareText, href: `/repos/${id}?tab=ask` },
    { key: 'architecture', label: 'Architecture', icon: Network, href: `/repos/${id}/graph` },
  ] as const

  return (
    <nav aria-label="Workspace" className="border-b border-border px-2 sm:px-4">
      <ul className="flex gap-1 overflow-x-auto max-sm:justify-between">
        {tabs.map(({ key, label, icon: Icon, href }) => {
          const isActive = active === key
          return (
            <li key={key}>
              <Link
                href={href}
                aria-current={isActive ? 'page' : undefined}
                className={cn(
                  'relative flex h-10 items-center gap-2 px-3 max-sm:px-3.5 text-sm whitespace-nowrap transition-colors',
                  'after:absolute after:inset-x-2 after:bottom-0 after:h-0.5 after:rounded-full after:transition-colors',
                  isActive
                    ? 'font-medium text-foreground after:bg-primary'
                    : 'text-muted-foreground after:bg-transparent hover:text-foreground',
                )}
              >
                <Icon className="size-4" aria-hidden="true" />
                <span className={cn(!isActive && 'max-sm:sr-only')}>{label}</span>
              </Link>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}
