'use client'

import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { useSWRConfig } from 'swr'
import { ArrowUp, History, Info, Loader2, MessageSquarePlus, Square } from 'lucide-react'
import { ChatMessageView } from '@/components/workspace/chat/chat-message'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import {
  ChatError,
  createConversation,
  getConversation,
  streamQuestion,
  type ChatTurnMessage,
  type ConversationSummary,
} from '@/lib/api/chat'
import { useConversations, useSearchIndex } from '@/lib/api/hooks'
import type { Repository } from '@/lib/types'
import { cn } from '@/lib/utils'

/** Mirrors the backend's `rag_question_max_chars` default. */
const MAX_QUESTION_CHARS = 2_000

const EXAMPLES = [
  'What does this project do and how is it structured?',
  'Where is the entry point?',
  'How are errors handled?',
  'How does authentication work?',
]

function isAbort(err: unknown) {
  return err instanceof DOMException && err.name === 'AbortError'
}

export function ChatPanel({ repo }: { repo: Repository }) {
  const { mutate } = useSWRConfig()
  const { data: history, error: historyError, isLoading: historyLoading } = useConversations(repo.id)
  const { data: index } = useSearchIndex(repo)

  const [activeId, setActiveId] = useState<number | null>(null)
  const [messages, setMessages] = useState<ChatTurnMessage[]>([])
  const [loadingConversation, setLoadingConversation] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)

  const abortRef = useRef<AbortController | null>(null)
  /** Bumped whenever the visible conversation changes or the panel unmounts; stale stream events are ignored. */
  const runRef = useRef(0)
  const scrollRef = useRef<HTMLDivElement>(null)
  const stickRef = useRef(true)

  const invalidateRun = useCallback(() => {
    runRef.current += 1
    abortRef.current?.abort()
    abortRef.current = null
  }, [])

  // Leaving the tab cancels the request; nothing is saved for an unfinished answer.
  useEffect(() => invalidateRun, [invalidateRun])

  useEffect(() => {
    const el = scrollRef.current
    if (el && stickRef.current) el.scrollTop = el.scrollHeight
  }, [messages])

  const refreshList = useCallback(() => mutate(['conversations', repo.id]), [mutate, repo.id])

  const send = useCallback(
    async (raw: string, options?: { dropLastTurn?: boolean }) => {
      const question = raw.trim()
      if (busy || abortRef.current) return // `abortRef` is set synchronously, so a double submit cannot start two requests
      if (!question) return setFormError('Type a question first.')
      if (question.length > MAX_QUESTION_CHARS) return setFormError(`Questions can be at most ${MAX_QUESTION_CHARS} characters.`)
      setFormError(null)
      setDraft('')

      invalidateRun()
      const run = runRef.current
      const controller = new AbortController()
      abortRef.current = controller
      const userId = `local-user-${run}`
      const answerId = `local-answer-${run}`
      const patchAnswer = (fn: (m: ChatTurnMessage) => ChatTurnMessage) => {
        if (runRef.current !== run) return
        setMessages((all) => all.map((m) => (m.id === answerId ? fn(m) : m)))
      }

      stickRef.current = true
      setBusy(true)
      setMessages((all) => [
        ...(options?.dropLastTurn ? all.slice(0, -2) : all),
        { id: userId, role: 'user', content: question, citations: [], status: 'complete' },
        { id: answerId, role: 'assistant', content: '', citations: [], status: 'searching' },
      ])

      try {
        let conversationId = activeId
        if (conversationId === null) {
          const created = await createConversation(repo.id)
          if (runRef.current !== run) return
          conversationId = created.id
          setActiveId(created.id)
          void refreshList()
        }
        await streamQuestion(
          repo.id,
          conversationId,
          question,
          {
            onRetrieval: (retrieval) => patchAnswer((m) => ({ ...m, retrieval, status: 'streaming' })),
            onToken: (text) => patchAnswer((m) => ({ ...m, content: m.content + text, status: 'streaming' })),
            onDone: ({ user, assistant }) => {
              if (runRef.current !== run) return
              setMessages((all) =>
                all.map((m) =>
                  m.id === userId ? user : m.id === answerId ? { ...assistant, retrieval: m.retrieval } : m,
                ),
              )
            },
          },
          controller.signal,
        )
        void refreshList()
      } catch (err) {
        if (runRef.current !== run) return // navigated away or switched conversation
        if (isAbort(err)) {
          patchAnswer((m) => ({ ...m, status: 'stopped' }))
        } else {
          const failure = err instanceof ChatError ? err : new ChatError('Something went wrong while answering.', 'failed')
          patchAnswer((m) => ({
            ...m,
            status: 'failed',
            error: { message: failure.message, code: failure.code, retryAfter: failure.retryAfter },
          }))
          if (failure.code === 'not_found') void refreshList()
        }
      } finally {
        if (runRef.current === run) {
          setBusy(false)
          abortRef.current = null
        }
      }
    },
    [activeId, busy, invalidateRun, refreshList, repo.id],
  )

  const startNewChat = useCallback(() => {
    invalidateRun()
    setBusy(false)
    setActiveId(null)
    setMessages([])
    setLoadError(null)
    setFormError(null)
    setDraft('')
    setHistoryOpen(false)
  }, [invalidateRun])

  const openConversation = useCallback(
    async (summary: ConversationSummary) => {
      setHistoryOpen(false)
      if (summary.id === activeId && !loadError) return
      invalidateRun()
      const run = runRef.current
      setBusy(false)
      setActiveId(summary.id)
      setMessages([])
      setLoadError(null)
      setFormError(null)
      setLoadingConversation(true)
      stickRef.current = true
      try {
        const loaded = await getConversation(repo.id, summary.id)
        if (runRef.current === run) setMessages(loaded.messages)
      } catch (err) {
        if (runRef.current === run) {
          setLoadError(err instanceof Error ? err.message : 'Could not load this conversation.')
          void refreshList()
        }
      } finally {
        if (runRef.current === run) setLoadingConversation(false)
      }
    },
    [activeId, invalidateRun, loadError, refreshList, repo.id],
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

  const noChunks = index !== undefined && index.chunks === 0
  const last = messages[messages.length - 1]
  const retryable = last?.role === 'assistant' && (last.status === 'failed' || last.status === 'stopped') && !busy
  const retry = retryable ? () => void send(messages[messages.length - 2]?.content ?? '', { dropLastTurn: true }) : undefined
  const indexNote = describeIndex(index)

  const list = (
    <ConversationList
      conversations={history}
      loading={historyLoading}
      failed={!!historyError}
      activeId={activeId}
      onNew={startNewChat}
      onOpen={openConversation}
    />
  )

  return (
    <div className="flex h-full min-h-0">
      <aside className="hidden w-64 shrink-0 flex-col border-r border-border bg-sidebar/50 lg:flex" aria-label="Conversations">
        {list}
      </aside>

      <Sheet open={historyOpen} onOpenChange={setHistoryOpen}>
        <SheetContent side="left" className="w-72 gap-0 p-0" showCloseButton={false}>
          <SheetTitle className="sr-only">Conversations</SheetTitle>
          <SheetDescription className="sr-only">Earlier questions about this repository</SheetDescription>
          {list}
        </SheetContent>
      </Sheet>

      <section className="flex min-w-0 flex-1 flex-col" aria-label="Ask AI">
        <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border px-3 lg:hidden">
          <Button variant="ghost" size="sm" onClick={() => setHistoryOpen(true)}>
            <History aria-hidden="true" />
            History
          </Button>
          <Button variant="ghost" size="sm" onClick={startNewChat}>
            <MessageSquarePlus aria-hidden="true" />
            New chat
          </Button>
        </div>

        <div
          ref={scrollRef}
          className="min-h-0 flex-1 overflow-y-auto"
          onScroll={(e) => {
            const el = e.currentTarget
            stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
          }}
        >
          <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-6">
            {loadingConversation ? (
              <div className="flex flex-col gap-4" role="status" aria-label="Loading conversation">
                <Skeleton className="ml-auto h-9 w-2/5 rounded-2xl" />
                <Skeleton className="h-24 w-full" />
              </div>
            ) : loadError ? (
              <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {loadError}{' '}
                <button className="underline underline-offset-2" onClick={startNewChat}>
                  Start a new chat
                </button>
              </div>
            ) : messages.length === 0 ? (
              <EmptyState repo={repo} disabled={busy || noChunks} onPick={(q) => void send(q)} />
            ) : (
              messages.map((m, i) => (
                <ChatMessageView
                  key={m.id}
                  message={m}
                  repoId={repo.id}
                  currentCommit={repo.indexedCommit}
                  onRetry={i === messages.length - 1 ? retry : undefined}
                />
              ))
            )}
          </div>
        </div>

        <div className="shrink-0 border-t border-border bg-background px-4 pt-3 pb-3">
          <div className="mx-auto flex w-full max-w-3xl flex-col gap-2">
            {noChunks ? (
              <Note tone="warning">
                This repository has no searchable code index yet. Use Re-analyze above to build it, then ask again.
              </Note>
            ) : (
              indexNote && <Note>{indexNote}</Note>
            )}
            <form onSubmit={handleSubmit} className="flex items-end gap-2">
              <Textarea
                value={draft}
                onChange={(e) => {
                  setDraft(e.target.value)
                  if (formError) setFormError(null)
                }}
                onKeyDown={handleKeyDown}
                rows={1}
                maxLength={MAX_QUESTION_CHARS}
                disabled={noChunks}
                placeholder={`Ask a question about ${repo.name}…`}
                aria-label="Your question"
                aria-invalid={!!formError}
                className="max-h-40 min-h-9 resize-none"
              />
              {busy ? (
                <Button type="button" size="icon-lg" variant="outline" onClick={() => abortRef.current?.abort()} aria-label="Stop generating">
                  <Square aria-hidden="true" />
                </Button>
              ) : (
                <Button type="submit" size="icon-lg" disabled={!draft.trim() || noChunks} aria-label="Send question">
                  <ArrowUp aria-hidden="true" />
                </Button>
              )}
            </form>
            {formError && (
              <p role="alert" className="text-xs text-destructive">
                {formError}
              </p>
            )}
            <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Info className="size-3.5 shrink-0" aria-hidden="true" />
              Answers are generated from retrieved code and can be wrong. Check the cited sources.
            </p>
          </div>
        </div>
      </section>
    </div>
  )
}

function describeIndex(index: ReturnType<typeof useSearchIndex>['data']): string | null {
  if (!index || index.chunks === 0) return null
  switch (index.semantic) {
    case 'disabled':
      return 'Semantic search is not configured, so answers use keyword matching.'
    case 'not_built':
      return 'The semantic index has not been built yet. Answers use keyword matching, which works best with exact names.'
    case 'partial':
      return `Semantic index ${index.embedded_chunks} of ${index.chunks} chunks. Questions still work and use keyword matching for the rest.`
    default:
      return null
  }
}

function Note({ children, tone }: { children: React.ReactNode; tone?: 'warning' }) {
  return (
    <p
      role="status"
      className={cn(
        'rounded-md border px-2.5 py-1.5 text-xs',
        tone === 'warning' ? 'border-warning/40 bg-warning/10 text-warning' : 'border-border bg-muted/40 text-muted-foreground',
      )}
    >
      {children}
    </p>
  )
}

function EmptyState({ repo, disabled, onPick }: { repo: Repository; disabled: boolean; onPick: (q: string) => void }) {
  return (
    <div className="flex flex-col gap-5 py-8">
      <div className="flex flex-col gap-1.5">
        <h2 className="text-xl font-semibold tracking-tight">Ask about {repo.name}</h2>
        <p className="text-sm text-muted-foreground">
          Answers come from this repository&apos;s code and cite the files and lines they used. Try one of these:
        </p>
      </div>
      <ul className="flex flex-col gap-2">
        {EXAMPLES.map((q) => (
          <li key={q}>
            <button
              disabled={disabled}
              onClick={() => onPick(q)}
              className="w-full rounded-lg border border-border bg-card px-4 py-3 text-left text-sm transition-colors hover:border-primary/40 hover:bg-accent/40 disabled:pointer-events-none disabled:opacity-50"
            >
              {q}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

function ConversationList({
  conversations,
  loading,
  failed,
  activeId,
  onNew,
  onOpen,
}: {
  conversations: ConversationSummary[] | undefined
  loading: boolean
  failed: boolean
  activeId: number | null
  onNew: () => void
  onOpen: (c: ConversationSummary) => void
}) {
  return (
    <>
      <div className="p-2">
        <Button variant="outline" size="sm" className="w-full justify-start" onClick={onNew}>
          <MessageSquarePlus aria-hidden="true" />
          New chat
        </Button>
      </div>
      <ul className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {loading && (
          <li className="flex items-center gap-2 px-2 py-3 text-xs text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
            Loading conversations…
          </li>
        )}
        {failed && <li className="px-2 py-3 text-xs text-destructive">Could not load conversations.</li>}
        {!loading && !failed && conversations?.length === 0 && (
          <li className="px-2 py-3 text-xs text-muted-foreground">No conversations yet. Ask a question to start one.</li>
        )}
        {conversations?.map((c) => (
          <li key={c.id}>
            <button
              onClick={() => onOpen(c)}
              aria-current={activeId === c.id ? 'true' : undefined}
              className={cn('w-full truncate rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent', activeId === c.id && 'bg-accent font-medium')}
            >
              {c.title}
            </button>
          </li>
        ))}
      </ul>
    </>
  )
}
