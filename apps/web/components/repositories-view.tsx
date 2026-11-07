'use client'

import { useState } from 'react'
import Link from 'next/link'
import { Plus, Search } from 'lucide-react'
import { RepositoryList } from '@/components/repository-list'
import { buttonVariants } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export function RepositoriesView() {
  const [filter, setFilter] = useState('')

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-10 sm:px-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-semibold tracking-tight">Repositories</h1>
          <p className="text-sm text-muted-foreground">Every repository you can open, with its ingestion status.</p>
        </div>
        <Link href="/dashboard" className={cn(buttonVariants({ variant: 'outline', size: 'sm' }))}>
          <Plus aria-hidden="true" />
          Import repository
        </Link>
      </div>
      <div className="flex h-9 max-w-sm items-center gap-2 rounded-md border border-border bg-card px-2.5 transition-colors focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/30">
        <Search className="size-4 text-muted-foreground" aria-hidden="true" />
        <label htmlFor="repo-filter" className="sr-only">
          Filter repositories
        </label>
        <input
          id="repo-filter"
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter by owner or name"
          className="h-full min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
        />
      </div>
      <RepositoryList filter={filter} />
    </div>
  )
}
