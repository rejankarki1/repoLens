'use client'

import { useState } from 'react'
import { Loader2, Lock, Plus } from 'lucide-react'
import { GitHubSignIn } from '@/components/auth/github-sign-in'
import { Button } from '@/components/ui/button'
import { importRepository } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/use-auth'
import type { Repository } from '@/lib/types'
import { mutate } from 'swr'

/** Adds a repository somebody already analysed to the user's own workspace (no new indexing). */
export function AddToWorkspace({ repo, size = 'sm' }: { repo: Repository; size?: 'sm' | 'default' }) {
  const { authenticated, loading } = useAuth()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (loading) return null
  if (!authenticated) return <GitHubSignIn next={`/repos/${repo.id}`} size={size} />

  async function add() {
    setBusy(true)
    setError(null)
    try {
      await importRepository(repo.url)
      await Promise.all([mutate(['repository', repo.id]), mutate('repositories')])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not add this repository.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col items-start gap-1">
      <Button size={size} onClick={add} disabled={busy}>
        {busy ? <Loader2 className="animate-spin" aria-hidden="true" /> : <Plus aria-hidden="true" />}
        Add to my repositories
      </Button>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}

/** Shown instead of Ask AI for guests: questions spend the AI budget, so they need a signed-in workspace member. */
export function AskRestricted({ repo }: { repo: Repository }) {
  const { authenticated, loading } = useAuth()
  return (
    <div className="flex h-full items-center justify-center px-4 py-12">
      <div className="flex max-w-md flex-col items-center gap-3 text-center">
        <div className="flex size-10 items-center justify-center rounded-lg bg-muted">
          <Lock className="size-5 text-muted-foreground" aria-hidden="true" />
        </div>
        <h2 className="text-base font-medium">Ask AI needs your own workspace</h2>
        <p className="text-sm text-muted-foreground">
          {loading || !authenticated
            ? `You are browsing ${repo.fullName} as a guest. Sign in with GitHub to ask questions about it; the source and architecture stay open to everyone.`
            : `Add ${repo.fullName} to your repositories to ask questions about it. Nothing is downloaded again; your conversations stay private to you.`}
        </p>
        <AddToWorkspace repo={repo} size="default" />
      </div>
    </div>
  )
}
