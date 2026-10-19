'use client'

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { useSWRConfig } from 'swr'
import { ArrowUp, Info, MessageSquarePlus, Square, Trash2 } from 'lucide-react'
import { ChatMessageView } from '@/components/workspace/chat/chat-message'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useConversations } from '@/lib/api/hooks'
import { askQuestion, createConversation, deleteConversation } from '@/lib/api/mock-client'
import type { Conversation, Repository } from '@/lib/types'
import { cn } from '@/lib/utils'

const EXAMPLES: Record<string, string[]> = {
  'facebook-react': ['How do hooks like useState work?', 'How are errors handled?', 'Where is the entry point?'],
}
const DEFAULT_EXAMPLES = ['Where is the entry point?', 'How does routing work?', 'How are errors handled?']

export function ChatPanel({ repo }: { repo: Repository }) {
  const { mutate } = useSWRConfig()
  const { data: history = [] } = useConversations(repo.id)
  const [active, setActive] = useState<Conversation | null>(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [active?.messages])
  useEffect(() => () => abortRef.current?.abort(), [])

  async function send(question: string) {
    const text = question.trim()
    if (!text || busy) return
    setDraft('')
    setBusy(true)
    const controller = new AbortController()
    abortRef.current = controller
    const conversation = active ?? createConversation(repo.id, text)
    try {
      await askQuestion(repo.id, conversation.id, text, { onUpdate: setActive, signal: controller.signal })
    } finally {
      setBusy(false)
      abortRef.current = null
      mutate(['conversations', repo.id])
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    send(draft)
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send(draft)
    }
  }

  function newChat() {
    abortRef.current?.abort()
    setActive(null)
    setDraft('')
  }

  const examples = EXAMPLES[repo.id] ?? DEFAULT_EXAMPLES

  return (
    <div className="flex h-full min-h-0">
      <aside className="hidden w-64 shrink-0 flex-col border-r border-border bg-sidebar/50 lg:flex" aria-label="Conversations">
        <div className="p-2">
          <Button variant="outline" size="sm" className="w-full justify-start" onClick={newChat}>
            <MessageSquarePlus aria-hidden="true" />
            New chat
          </Button>
        </div>
        <ul className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
          {history.length === 0 && <li className="px-2 py-3 text-xs text-muted-foreground">Conversations from this session appear here.</li>}
          {history.map((c) => (
            <li key={c.id} className="group relative">
              <button
                onClick={() => !busy && setActive(c)}
                className={cn(
                  'w-full truncate rounded-md px-2 py-1.5 pr-8 text-left text-sm hover:bg-accent',
                  active?.id === c.id && 'bg-accent font-medium',
                )}
              >
                {c.title}
              </button>
              <Button
                variant="ghost"
                size="icon-xs"
                className="absolute top-1/2 right-1 -translate-y-1/2 opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                aria-label={`Delete conversation ${c.title}`}
                onClick={() => {
                  deleteConversation(repo.id, c.id)
                  if (active?.id === c.id) setActive(null)
                  mutate(['conversations', repo.id])
                }}
              >
                <Trash2 aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ul>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col" aria-label="Ask AI">
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-6">
            {!active || active.messages.length === 0 ? (
              <div className="flex flex-col gap-5 py-8">
                <div className="flex flex-col gap-1.5">
                  <h2 className="text-xl font-semibold tracking-tight">Ask about {repo.name}</h2>
                  <p className="text-sm text-muted-foreground">
                    Answers cite the files and lines they came from. Try one of these:
                  </p>
                </div>
                <ul className="flex flex-col gap-2">
                  {examples.map((q) => (
                    <li key={q}>
                      <button
                        onClick={() => send(q)}
                        className="w-full rounded-lg border border-border bg-card px-4 py-3 text-left text-sm transition-colors hover:border-primary/40 hover:bg-accent/40"
                      >
                        {q}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              active.messages.map((m) => <ChatMessageView key={m.id} message={m} repoId={repo.id} />)
            )}
            <div ref={endRef} />
          </div>
        </div>

        <div className="shrink-0 border-t border-border bg-background px-4 pt-3 pb-3">
          <form onSubmit={handleSubmit} className="mx-auto flex w-full max-w-3xl items-end gap-2">
            <Textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={handleKeyDown}
              rows={1}
              placeholder={`Ask a question about ${repo.name}…`}
              aria-label="Your question"
              className="max-h-40 min-h-9 resize-none"
            />
            {busy ? (
              <Button type="button" size="icon-lg" variant="outline" onClick={() => abortRef.current?.abort()} aria-label="Stop generating">
                <Square aria-hidden="true" />
              </Button>
            ) : (
              <Button type="submit" size="icon-lg" disabled={!draft.trim()} aria-label="Send question">
                <ArrowUp aria-hidden="true" />
              </Button>
            )}
          </form>
          <p className="mx-auto mt-2 flex max-w-3xl items-center gap-1.5 text-xs text-muted-foreground">
            <Info className="size-3.5 shrink-0" aria-hidden="true" />
            Demo mode: responses are canned mock answers matched by keyword, not real AI analysis.
          </p>
        </div>
      </section>
    </div>
  )
}
