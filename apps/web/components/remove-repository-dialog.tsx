'use client'

import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import {
  AlertDialog,
  AlertDialogClose,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { removeRepository } from '@/lib/api/client'
import type { Repository } from '@/lib/types'

/**
 * Confirms and performs the removal of a repository from the signed-in user's workspace. The source repository on
 * GitHub is never involved; the backend decides whether the stored copy goes too (only if nobody else has it).
 */
export function RemoveRepositoryDialog({
  repository,
  onClose,
  onRemoved,
}: {
  repository: Repository | null
  onClose: () => void
  onRemoved: (repository: Repository) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function close() {
    if (busy) return
    setError(null)
    onClose()
  }

  async function confirm() {
    if (!repository) return
    setBusy(true)
    setError(null)
    try {
      await removeRepository(repository.id)
      onRemoved(repository)
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not remove this repository.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <AlertDialog open={repository !== null} onOpenChange={(open) => !open && close()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Remove {repository?.fullName}?</AlertDialogTitle>
          <AlertDialogDescription>
            This removes the repository and your Ask AI conversations about it from your workspace. Its stored copy is deleted from RepoLens
            unless someone else has also added it. Your repository on GitHub is not affected, and you can import it again at any time.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error && (
          <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {error}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogClose render={<Button variant="outline" disabled={busy} />}>Cancel</AlertDialogClose>
          <Button variant="destructive" onClick={confirm} disabled={busy}>
            {busy && <Loader2 className="animate-spin" aria-hidden="true" />}
            Remove repository
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
