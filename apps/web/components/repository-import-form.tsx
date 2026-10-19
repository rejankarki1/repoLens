'use client'

import { useState, type FormEvent } from 'react'
import { useRouter } from 'next/navigation'
import { useSWRConfig } from 'swr'
import { ArrowRight, Loader2 } from 'lucide-react'
import { GitHubIcon } from '@/components/icons'
import { Button } from '@/components/ui/button'
import { ApiError, importRepository, listRepositories, startIngestion } from '@/lib/api/client'
import { parseGitHubUrl } from '@/lib/repo-utils'
import { cn } from '@/lib/utils'

const EXAMPLES = ['github.com/octocat/Hello-World', 'github.com/pallets/flask', 'github.com/honojs/hono']

export function RepositoryImportForm() {
  const router = useRouter()
  const { mutate } = useSWRConfig()
  const [value, setValue] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setNotice(null)
    const parsed = parseGitHubUrl(value)
    if (!parsed.ok) {
      setError(parsed.error)
      return
    }
    setError(null)
    setSubmitting(true)
    try {
      // The API wants a full URL; the form also accepts "owner/name" and bare github.com URLs.
      const repo = await importRepository(`https://github.com/${parsed.owner}/${parsed.name}`)
      await mutate('repositories')
      // Tracked by the client's ingestion registry; progress is shown in the workspace via polling.
      startIngestion(repo.id).catch(() => undefined)
      setNotice(`Imported ${repo.fullName}. Ingesting source files…`)
      setValue('')
      router.push(`/repos/${repo.id}`)
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Already imported: take the user to the existing workspace instead of failing.
        const existing = (await listRepositories().catch(() => [])).find(
          (r) => r.fullName.toLowerCase() === `${parsed.owner}/${parsed.name}`.toLowerCase(),
        )
        if (existing) {
          setValue('')
          router.push(`/repos/${existing.id}`)
          return
        }
      }
      setError(err instanceof Error ? err.message : 'Import failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex flex-col gap-2.5">
      <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-2 sm:flex-row">
        <label htmlFor="repo-url" className="sr-only">
          GitHub repository URL
        </label>
        <div
          className={cn(
            'flex h-11 min-w-0 flex-1 items-center gap-2.5 rounded-lg border bg-card px-3 transition-colors focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/30',
            error ? 'border-destructive' : 'border-border',
          )}
        >
          <GitHubIcon className="size-4 shrink-0 text-muted-foreground" />
          <input
            id="repo-url"
            type="url"
            inputMode="url"
            autoComplete="off"
            spellCheck={false}
            placeholder="https://github.com/owner/repository"
            value={value}
            onChange={(e) => {
              setValue(e.target.value)
              if (error) setError(null)
            }}
            aria-invalid={Boolean(error)}
            aria-describedby={error ? 'repo-url-error' : 'repo-url-hint'}
            className="h-full min-w-0 flex-1 bg-transparent font-mono text-sm outline-none placeholder:text-muted-foreground/70"
          />
        </div>
        <Button type="submit" size="lg" className="h-11 px-4" disabled={submitting}>
          {submitting ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
          Analyze Repository
          {!submitting && <ArrowRight aria-hidden="true" />}
        </Button>
      </form>

      <div aria-live="polite" className="min-h-5 text-xs">
        {error ? (
          <p id="repo-url-error" className="text-destructive">
            {error}
          </p>
        ) : notice ? (
          <p className="text-success">{notice}</p>
        ) : (
          <p id="repo-url-hint" className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-muted-foreground">
            <span>Try</span>
            {EXAMPLES.map((example, i) => (
              <span key={example} className="inline-flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => setValue(`https://${example}`)}
                  className="rounded font-mono text-foreground/80 underline-offset-2 transition-colors hover:text-primary hover:underline"
                >
                  {example.replace('github.com/', '')}
                </button>
                {i < EXAMPLES.length - 1 && <span aria-hidden="true">·</span>}
              </span>
            ))}
          </p>
        )}
      </div>
    </div>
  )
}
