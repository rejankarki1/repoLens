'use client'

import { useCallback, useState } from 'react'
import { Info, Lock } from 'lucide-react'
import { AddToWorkspace, AskRestricted } from '@/components/auth/access-prompt'
import { ChatMessageView } from '@/components/workspace/chat/chat-message'
import { SourcePreview } from '@/components/workspace/chat/source-preview'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import type { SourceCitation } from '@/lib/api/chat'
import { useShowcaseAnswers } from '@/lib/api/hooks'
import { useAuth } from '@/lib/auth/use-auth'
import type { Repository } from '@/lib/types'
import { useMediaQuery } from '@/lib/use-media-query'

/**
 * What a visitor (or a signed-in user who has not added the repository) sees in place of Ask AI: the curated, real
 * answers saved for this showcase repository, with working citations. Nothing here calls the AI.
 */
export function ShowcaseAnswers({ repo }: { repo: Repository }) {
  const { data, error, isLoading } = useShowcaseAnswers(repo.id)
  const { authenticated, loading: authLoading } = useAuth()
  const [preview, setPreview] = useState<{ messageId: string; citation: SourceCitation } | null>(null)
  const sideBySide = useMediaQuery('(min-width: 1280px)')
  const tablet = useMediaQuery('(min-width: 640px)')
  const openPreview = useCallback((citation: SourceCitation, messageId: string) => setPreview({ messageId, citation }), [])
  const closePreview = useCallback(() => setPreview(null), [])

  if (isLoading) {
    return (
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-4 py-6" role="status" aria-label="Loading example answers">
        <Skeleton className="ml-auto h-9 w-2/5 rounded-2xl" />
        <Skeleton className="h-28 w-full" />
      </div>
    )
  }
  if (error || !data || data.length === 0) return <AskRestricted repo={repo} />

  return (
    <div className="flex h-full min-h-0">
      <div className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-6">
          <div className="flex flex-col gap-1.5">
            <h2 className="text-xl font-semibold tracking-tight">Example questions about {repo.name}</h2>
            <p className="text-sm text-muted-foreground">
              Real answers RepoLens generated from this repository&apos;s code, saved for the demo. Select a source tag to see the cited lines.
            </p>
          </div>

          {data.map((example) => (
            <div key={example.id} className="flex flex-col gap-4">
              <ChatMessageView message={example.question} repoId={repo.id} currentCommit={repo.indexedCommit} />
              <ChatMessageView
                message={example.answer}
                repoId={repo.id}
                currentCommit={repo.indexedCommit}
                onPreview={openPreview}
                previewed={preview ? { messageId: preview.messageId, citationId: preview.citation.id } : null}
              />
            </div>
          ))}

          <div className="flex flex-col items-start gap-3 rounded-lg border border-border bg-card p-4">
            <p className="flex items-start gap-2 text-sm text-muted-foreground">
              <Lock className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              {authLoading || !authenticated
                ? 'Asking your own questions needs a GitHub sign-in, because every answer uses the AI budget. The code and architecture stay open to everyone.'
                : `Add ${repo.fullName} to your repositories to ask your own questions. Nothing is downloaded again, and your conversations stay private.`}
            </p>
            <AddToWorkspace repo={repo} size="default" />
          </div>
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Info className="size-3.5 shrink-0" aria-hidden="true" />
            Answers are generated from retrieved code and can be wrong. Check the cited sources.
          </p>
        </div>
      </div>

      {preview && sideBySide && (
        <aside className="flex w-[44%] max-w-[46rem] min-w-[26rem] shrink-0 border-l border-border" aria-label="Source preview">
          <div className="min-w-0 flex-1">
            <SourcePreview repo={repo} citation={preview.citation} onClose={closePreview} closeOnEscape />
          </div>
        </aside>
      )}
      {preview && !sideBySide && (
        <Sheet open onOpenChange={(open) => !open && closePreview()}>
          <SheetContent
            side={tablet ? 'right' : 'bottom'}
            showCloseButton={false}
            className="gap-0 p-0 data-[side=bottom]:h-[88dvh] data-[side=bottom]:rounded-t-xl data-[side=right]:sm:max-w-2xl"
          >
            <SheetTitle className="sr-only">Source preview: {preview.citation.path}</SheetTitle>
            <SheetDescription className="sr-only">The cited lines of the file, highlighted</SheetDescription>
            <SourcePreview repo={repo} citation={preview.citation} onClose={closePreview} closeOnEscape={false} />
          </SheetContent>
        </Sheet>
      )}
    </div>
  )
}
