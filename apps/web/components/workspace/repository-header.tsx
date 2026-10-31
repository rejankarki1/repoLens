'use client'

import Link from 'next/link'
import { useState } from 'react'
import { ExternalLink, GitBranch, GitCommitHorizontal, RefreshCw } from 'lucide-react'
import { GitHubIcon } from '@/components/icons'
import { IndexingProgressBar, IndexingStatus } from '@/components/indexing-status'
import { LanguageDot } from '@/components/language-dot'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useRepository } from '@/lib/api/hooks'
import { describeIngestionError, startIngestion } from '@/lib/api/client'

export function RepositoryHeader({ id }: { id: string }) {
  const { data: repo, isLoading } = useRepository(id)
  const [reanalyzing, setReanalyzing] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  if (isLoading) {
    return (
      <header className="flex flex-col gap-3 border-b border-border px-4 py-4 sm:px-6">
        <Skeleton className="h-3.5 w-40" />
        <Skeleton className="h-6 w-56" />
      </header>
    )
  }
  if (!repo) return null

  const busy = repo.status === 'queued' || repo.status === 'importing' || repo.status === 'indexing'

  async function handleReanalyze() {
    // Local guard against double clicks; the backend's 409 is the authoritative duplicate check.
    setReanalyzing(true)
    setNotice(null)
    try {
      await startIngestion(id)
    } catch (err) {
      setNotice(describeIngestionError(err).message)
    } finally {
      setReanalyzing(false)
    }
  }

  return (
    <header className="flex flex-col gap-3 border-b border-border px-4 pt-4 pb-3 sm:px-6">
      <nav aria-label="Breadcrumb">
        <ol className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <li>
            <Link href="/repositories" className="transition-colors hover:text-foreground">
              Repositories
            </Link>
          </li>
          <li aria-hidden="true">/</li>
          <li>{repo.owner}</li>
          <li aria-hidden="true">/</li>
          <li aria-current="page" className="text-foreground">
            {repo.name}
          </li>
        </ol>
      </nav>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <GitHubIcon className="size-5 shrink-0" />
          <h1 className="truncate text-lg font-semibold tracking-tight">
            <span className="font-normal text-muted-foreground">{repo.owner} / </span>
            {repo.name}
          </h1>
        </div>

        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-muted-foreground">
          <IndexingStatus status={repo.status} progress={repo.progress} />
          {repo.primaryLanguage !== 'Unknown' && (
            <span className="inline-flex items-center gap-1.5">
              <LanguageDot language={repo.primaryLanguage} />
              {repo.primaryLanguage}
            </span>
          )}
          <span className="inline-flex items-center gap-1">
            <GitBranch className="size-3.5" aria-hidden="true" />
            <span className="font-mono">{repo.defaultBranch}</span>
          </span>
          {repo.indexedCommit && (
            <Tooltip>
              <TooltipTrigger
                render={
                  <span className="inline-flex cursor-default items-center gap-1 rounded bg-muted px-1.5 py-0.5 font-mono" />
                }
              >
                <GitCommitHorizontal className="size-3.5" aria-hidden="true" />
                {repo.indexedCommit}
              </TooltipTrigger>
              <TooltipContent>Ingested commit</TooltipContent>
            </Tooltip>
          )}
        </div>

        <div className="ml-auto flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            render={<a href={repo.url} target="_blank" rel="noreferrer" />}
            nativeButton={false}
          >
            <ExternalLink aria-hidden="true" />
            <span className="max-sm:sr-only">GitHub</span>
          </Button>
          <Button variant="outline" size="sm" onClick={handleReanalyze} disabled={busy || reanalyzing}>
            <RefreshCw className={busy || reanalyzing ? 'animate-spin' : undefined} aria-hidden="true" />
            <span className="max-sm:sr-only">Re-analyze</span>
          </Button>
        </div>
      </div>

      {notice && (
        <p role="alert" className="text-xs text-destructive">
          {notice}
        </p>
      )}

      {busy && <IndexingProgressBar status="indexing" progress={repo.progress} />}
    </header>
  )
}
