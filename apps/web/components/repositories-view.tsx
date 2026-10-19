'use client'

import { useState } from 'react'
import { Search } from 'lucide-react'
import { RepositoryImportForm } from '@/components/repository-import-form'
import { RepositoryList } from '@/components/repository-list'

export function RepositoriesView() {
  const [filter, setFilter] = useState('')

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-8 px-4 py-10 sm:px-8">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Repositories</h1>
        <p className="text-sm text-muted-foreground">Public GitHub repositories you have imported.</p>
      </div>
      <RepositoryImportForm />
      <div className="flex flex-col gap-4">
        <div className="flex h-9 max-w-sm items-center gap-2 rounded-md border border-border bg-card px-2.5 focus-within:border-ring">
          <Search className="size-4 text-muted-foreground" aria-hidden="true" />
          <label htmlFor="repo-filter" className="sr-only">
            Filter repositories
          </label>
          <input
            id="repo-filter"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter by owner or name"
            className="h-full flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
        </div>
        <RepositoryList filter={filter} />
      </div>
    </div>
  )
}
