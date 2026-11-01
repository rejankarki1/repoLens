'use client'

import { Suspense, useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { useSWRConfig } from 'swr'
import { ArrowRight, Loader2 } from 'lucide-react'
import { GitHubSignIn } from '@/components/auth/github-sign-in'
import { GitHubIcon } from '@/components/icons'
import { Button } from '@/components/ui/button'
import { importRepository, startIngestion } from '@/lib/api/client'
import { useRepositories } from '@/lib/api/hooks'
import { authErrorMessage, pendingImport, resumePath } from '@/lib/auth/import-resume'
import { useAuth } from '@/lib/auth/use-auth'
import { parseGitHubUrl } from '@/lib/repo-utils'
import { cn } from '@/lib/utils'

const EXAMPLES = ['github.com/octocat/Hello-World', 'github.com/pallets/flask', 'github.com/honojs/hono']

export function RepositoryImportForm() {
  return (
    <Suspense fallback={<div className="h-[4.75rem]" aria-hidden="true" />}>
      <ImportForm />
    </Suspense>
  )
}

function ImportForm() {
  const router = useRouter()
  const { mutate } = useSWRConfig()
  const { authenticated, loading: authLoading } = useAuth()
  const { data: visible } = useRepositories()
  const params = useSearchParams().toString()
  // What the page URL carried when it opened: a sign-in failure, or the repository pasted before signing in.
  const [arrival] = useState(() => ({ failure: authErrorMessage(params), pending: pendingImport(params) }))
  const [value, setValue] = useState(arrival.pending ?? '')
  const [error, setError] = useState<string | null>(arrival.failure)
  const [submitting, setSubmitting] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  // A visitor who pasted a repository outside the showcase must sign in to import it.
  const [signInRequested, setSignInRequested] = useState<string | null>(null)
  const signInFor = signInRequested ?? (arrival.pending && !authLoading && !authenticated ? arrival.pending : null)
  const resumed = useRef(false)

  const importUrl = useCallback(
    async (url: string) => {
      const parsed = parseGitHubUrl(url)
      if (!parsed.ok) {
        setError(parsed.error)
        return
      }
      setError(null)
      setSignInRequested(null)
      const fullName = `${parsed.owner}/${parsed.name}`

      if (!authenticated) {
        // A showcase repository can be opened without an account; anything else needs sign-in to be imported.
        const showcased = visible?.find((r) => r.fullName.toLowerCase() === fullName.toLowerCase())
        if (showcased) {
          router.push(`/repos/${showcased.id}`)
          return
        }
        setSignInRequested(`https://github.com/${fullName}`)
        return
      }

      setSubmitting(true)
      try {
        // The API wants a full URL; the form also accepts "owner/name" and bare github.com URLs. A repository somebody
        // already analysed is simply added to this workspace (nothing is downloaded again).
        const repo = await importRepository(`https://github.com/${fullName}`)
        await mutate('repositories')
        if (repo.status === 'pending') {
          // Returns as soon as the job is queued (202); the workspace shows progress by polling.
          await startIngestion(repo.id).catch(() => undefined)
          setNotice(`Imported ${repo.fullName}. Ingestion is running in the background.`)
        } else {
          setNotice(`Added ${repo.fullName} to your repositories.`)
        }
        setValue('')
        router.push(`/repos/${repo.id}`)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Import failed')
      } finally {
        setSubmitting(false)
      }
    },
    [authenticated, mutate, router, visible],
  )

  // Back from GitHub signed in: import the repository that was pasted before signing in, exactly once.
  useEffect(() => {
    const pending = arrival.pending
    if (authLoading || !authenticated || !pending) return
    const timer = setTimeout(() => {
      if (resumed.current) return
      resumed.current = true
      void importUrl(pending)
    }, 0)
    return () => clearTimeout(timer)
  }, [arrival.pending, authLoading, authenticated, importUrl])

  // The page URL has done its job; keep it clean so a reload does not repeat the import.
  useEffect(() => {
    if (arrival.failure || arrival.pending) window.history.replaceState(null, '', window.location.pathname)
  }, [arrival])

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setNotice(null)
    await importUrl(value)
  }

  return (
    <div className="flex flex-col gap-2.5">
      <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-2 sm:flex-row">
        <label htmlFor="repo-url" className="sr-only">
          GitHub repository URL
        </label>
        <div
          className={cn(
            'flex h-11 min-w-0 shrink-0 items-center sm:flex-1 gap-2.5 rounded-lg border bg-card px-3 transition-colors focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/30',
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
        <Button type="submit" size="lg" className="h-11 px-4" disabled={submitting || authLoading}>
          {submitting ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
          Analyze Repository
          {!submitting && <ArrowRight aria-hidden="true" />}
        </Button>
      </form>

      {signInFor && (
        <div role="status" className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm">
            <span className="font-medium">Sign in to analyze {signInFor.replace('https://github.com/', '')}.</span>{' '}
            <span className="text-muted-foreground">
              We only read your public GitHub profile to know who you are; nothing else is requested. Your pasted repository is
              analyzed as soon as you return.
            </span>
          </p>
          <GitHubSignIn next={resumePath(signInFor)} className="shrink-0" />
        </div>
      )}

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
