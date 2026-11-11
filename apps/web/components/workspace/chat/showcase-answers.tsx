'use client'

import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { useSWRConfig } from 'swr'
import { ArrowUp, Info, Square } from 'lucide-react'
import { AddToWorkspace, AskRestricted } from '@/components/auth/access-prompt'
import { GitHubSignIn } from '@/components/auth/github-sign-in'
import { ChatMessageView } from '@/components/workspace/chat/chat-message'
import { SourcePreview } from '@/components/workspace/chat/source-preview'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { ChatError, streamGuestQuestion, type ChatTurnMessage, type SourceCitation } from '@/lib/api/chat'
import { ApiError } from '@/lib/api/client'
import { describeGuestError, describeGuestQuota, GUEST_MAX_QUESTION_CHARS } from '@/lib/api/guest-ask-ui'
import { useGuestAsk, useShowcaseAnswers } from '@/lib/api/hooks'
import { useAuth } from '@/lib/auth/use-auth'
import type { Repository } from '@/lib/types'
import { useMediaQuery } from '@/lib/use-media-query'

/**
 * What a visitor (or a signed-in user who has not added the repository) sees in place of Ask AI: the curated, real
 * answers saved for this showcase repository, and, when the server allows it, a few free live questions. Free
 * questions are answered from the same code index with the same citations, are never saved, and are capped on the
 * server; the saved examples never call the AI.
 */
export function ShowcaseAnswers({ repo }: { repo: Repository }) {
  const { data, error, isLoading } = useShowcaseAnswers(repo.id)
  const { data: guestAsk } = useGuestAsk(repo.id)
  const { mutate } = useSWRConfig()
  const { authenticated, loginEnabled } = useAuth()
  const [preview, setPreview] = useState<{ messageId: string; citation: SourceCitation } | null>(null)
  const [asked, setAsked] = useState<ChatTurnMessage[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const sideBySide = useMediaQuery('(min-width: 1280px)')
  const tablet = useMediaQuery('(min-width: 640px)')
  const openPreview = useCallback((citation: SourceCitation, messageId: string) => setPreview({ messageId, citation }), [])
  const closePreview = useCallback(() => setPreview(null), [])

  useEffect(() => () => abortRef.current?.abort(), [])
  useEffect(() => {
    const el = scrollRef.current
    if (el && asked.length > 0) el.scrollTop = el.scrollHeight
  }, [asked])

  const quota = guestAsk?.enabled ? describeGuestQuota(guestAsk) : null

  const send = useCallback(
    async (text: string) => {
      const question = text.trim()
      if (!question || busy) return
      const stamp = Date.now()
      const userId = `gu${stamp}`
      const answerId = `ga${stamp}`
      const controller = new AbortController()
      abortRef.current = controller
      const patch = (fn: (m: ChatTurnMessage) => ChatTurnMessage) => setAsked((all) => all.map((m) => (m.id === answerId ? fn(m) : m)))
      setBusy(true)
      setDraft('')
      setAsked((all) => [
        ...all,
        { id: userId, role: 'user', content: question, citations: [], status: 'complete' },
        { id: answerId, role: 'assistant', content: '', citations: [], status: 'searching' },
      ])
      try {
        await streamGuestQuestion(
          repo.id,
          question,
          {
            onRetrieval: (retrieval) => patch((m) => ({ ...m, retrieval, status: 'streaming' })),
            onToken: (t) => patch((m) => ({ ...m, content: m.content + t, status: 'streaming' })),
            onDone: ({ assistant }) => patch((m) => ({ ...assistant, id: answerId, retrieval: m.retrieval })),
          },
          controller.signal,
        )
      } catch (err) {
        if (controller.signal.aborted) {
          patch((m) => ({ ...m, status: 'stopped' }))
        } else {
          const failure =
            err instanceof ChatError
              ? err
              : err instanceof ApiError
                ? new ChatError(err.message, err.status === 0 ? 'network' : 'failed')
                : new ChatError('Something went wrong while answering.', 'failed')
          patch((m) => ({
            ...m,
            status: 'failed',
            error: { message: describeGuestError(failure), code: failure.code, retryAfter: failure.retryAfter },
          }))
        }
      } finally {
        setBusy(false)
        abortRef.current = null
        void mutate(['guest-ask', repo.id]) // the server owns the count, including a question that failed after it was paid for
      }
    },
    [busy, mutate, repo.id],
  )

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    void send(draft)
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      void send(draft)
    }
  }

  if (isLoading) {
    return (
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-4 py-6" role="status" aria-label="Loading example answers">
        <Skeleton className="ml-auto h-9 w-2/5 rounded-2xl" />
        <Skeleton className="h-28 w-full" />
      </div>
    )
  }
  const examples = error ? [] : (data ?? [])
  if (examples.length === 0 && !quota) return <AskRestricted repo={repo} />

  return (
    <div className="flex h-full min-h-0">
      <section className="flex min-w-0 flex-1 flex-col" aria-label="Ask AI">
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-6">
            {!authenticated && loginEnabled ? (
              <div role="note" className="flex flex-col gap-3 rounded-lg border border-primary/30 bg-primary/5 p-4 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-sm">
                  <span className="font-medium">Sign in with GitHub to analyze your own repositories</span>
                  <span className="text-muted-foreground"> and ask without the daily limit. We only read your public profile.</span>
                </p>
                <GitHubSignIn next={`/repos/${repo.id}?tab=ask`} size="lg" label="Sign in with GitHub" className="shrink-0" />
              </div>
            ) : authenticated ? (
              <div role="note" className="flex flex-col gap-3 rounded-lg border border-primary/30 bg-primary/5 p-4 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-sm text-muted-foreground">
                  Add {repo.fullName} to your repositories to ask without the daily limit and keep your conversations.
                </p>
                <AddToWorkspace repo={repo} size="default" />
              </div>
            ) : null}

            <div className="flex flex-col gap-1.5">
              <h2 className="text-xl font-semibold tracking-tight">{quota ? `Ask about ${repo.name}` : `Example questions about ${repo.name}`}</h2>
              <p className="text-sm text-muted-foreground">
                {examples.length > 0
                  ? 'Real answers RepoLens generated from this repository’s code, saved for the demo. Select a source tag to see the cited lines.'
                  : 'Answers come from this repository’s code and cite the files and lines they used.'}
                {quota && ' You can also ask a few questions of your own below, no account needed.'}
              </p>
            </div>

            {examples.map((example) => (
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

            {asked.length > 0 && (
              <div className="flex flex-col gap-4 border-t border-border pt-6" aria-label="Your questions">
                {asked.map((m) => (
                  <ChatMessageView
                    key={m.id}
                    message={m}
                    repoId={repo.id}
                    currentCommit={repo.indexedCommit}
                    onPreview={openPreview}
                    previewed={preview ? { messageId: preview.messageId, citationId: preview.citation.id } : null}
                  />
                ))}
                <p className="text-xs text-muted-foreground">These questions are not saved. Reloading the page clears them.</p>
              </div>
            )}

            {!quota && (
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Info className="size-3.5 shrink-0" aria-hidden="true" />
                Answers are generated from retrieved code and can be wrong. Check the cited sources.
              </p>
            )}
          </div>
        </div>

        {quota && (
          <div className="shrink-0 border-t border-border bg-background px-4 pt-3 pb-3">
            <div className="mx-auto flex w-full max-w-3xl flex-col gap-2">
              <form onSubmit={handleSubmit} className="flex items-end gap-2">
                <Textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={handleKeyDown}
                  rows={1}
                  maxLength={GUEST_MAX_QUESTION_CHARS}
                  disabled={!quota.canAsk}
                  placeholder={quota.canAsk ? `Ask a question about ${repo.name}…` : 'No free questions left today'}
                  aria-label="Your question"
                  className="max-h-40 min-h-9 resize-none"
                />
                {busy ? (
                  <Button type="button" size="icon-lg" variant="outline" onClick={() => abortRef.current?.abort()} aria-label="Stop generating">
                    <Square aria-hidden="true" />
                  </Button>
                ) : (
                  <Button type="submit" size="icon-lg" disabled={!draft.trim() || !quota.canAsk} aria-label="Send question">
                    <ArrowUp aria-hidden="true" />
                  </Button>
                )}
              </form>
              <p className="flex flex-wrap items-center gap-x-1.5 text-xs text-muted-foreground">
                <Info className="size-3.5 shrink-0" aria-hidden="true" />
                <span role="status" className="font-medium text-foreground">
                  {quota.text}
                </span>
                <span>· Answers are generated from retrieved code and can be wrong. Check the cited sources.</span>
              </p>
            </div>
          </div>
        )}
      </section>

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
