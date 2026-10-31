'use client'

import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Check, Loader2, MessageSquarePlus, Pencil, Trash2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { ConversationSummary } from '@/lib/api/chat'
import { formatRelativeTime } from '@/lib/repo-utils'
import { cn } from '@/lib/utils'

interface Props {
  conversations: ConversationSummary[] | undefined
  loading: boolean
  failed: boolean
  activeId: number | null
  onNew: () => void
  onOpen: (c: ConversationSummary) => void
  /** Each action resolves when done and rejects with a user-facing message. */
  onRename: (c: ConversationSummary, title: string) => Promise<void>
  onDelete: (c: ConversationSummary) => Promise<void>
  onClear: () => Promise<void>
}

/** Messages come in question/answer pairs. */
const questionCount = (messages: number) => {
  const questions = Math.ceil(messages / 2)
  return `${questions} ${questions === 1 ? 'question' : 'questions'}`
}

const ACTION =
  'size-6 text-muted-foreground opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100 [@media(hover:none)]:opacity-100'

export function ConversationList({ conversations, loading, failed, activeId, onNew, onOpen, onRename, onDelete, onClear }: Props) {
  const [editing, setEditing] = useState<number | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null)
  const [confirmClear, setConfirmClear] = useState(false)
  const [pending, setPending] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function run(key: string, action: () => Promise<void>, done: () => void) {
    setPending(key)
    setError(null)
    try {
      await action()
      done()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong.')
    } finally {
      setPending(null)
    }
  }

  const items = conversations ?? []

  return (
    <>
      <div className="p-2">
        <Button variant="outline" size="sm" className="w-full justify-start" onClick={onNew}>
          <MessageSquarePlus aria-hidden="true" />
          New chat
        </Button>
      </div>

      <ul className="min-h-0 flex-1 overflow-y-auto px-2 pb-2" aria-label="Conversation history">
        {loading && (
          <li className="flex items-center gap-2 px-2 py-3 text-xs text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
            Loading conversations…
          </li>
        )}
        {failed && <li className="px-2 py-3 text-xs text-destructive">Could not load conversations.</li>}
        {!loading && !failed && items.length === 0 && (
          <li className="px-2 py-3 text-xs leading-relaxed text-muted-foreground">
            No conversations yet. Ask a question and it is saved here, so you can come back to it.
          </li>
        )}
        {items.map((c) => (
          <li key={c.id} className="group relative">
            {editing === c.id ? (
              <RenameRow
                initial={c.title === 'Untitled chat' ? '' : c.title}
                busy={pending === `rename:${c.id}`}
                onCancel={() => setEditing(null)}
                onSave={(title) => void run(`rename:${c.id}`, () => onRename(c, title), () => setEditing(null))}
              />
            ) : confirmDelete === c.id ? (
              <div className="flex items-center gap-1 rounded-md bg-destructive/10 px-2 py-1.5 text-xs" role="group" aria-label={`Delete ${c.title}?`}>
                <span className="min-w-0 flex-1 truncate text-destructive">Delete this chat?</span>
                <Button
                  size="xs"
                  variant="destructive"
                  disabled={pending === `delete:${c.id}`}
                  onClick={() => void run(`delete:${c.id}`, () => onDelete(c), () => setConfirmDelete(null))}
                >
                  {pending === `delete:${c.id}` && <Loader2 className="animate-spin" aria-hidden="true" />}
                  Delete
                </Button>
                <Button size="xs" variant="ghost" onClick={() => setConfirmDelete(null)}>
                  Keep
                </Button>
              </div>
            ) : (
              <>
                <button
                  type="button"
                  onClick={() => onOpen(c)}
                  aria-current={activeId === c.id ? 'true' : undefined}
                  className={cn(
                    'flex w-full flex-col rounded-md px-2 py-1.5 pr-14 text-left hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none',
                    activeId === c.id && 'bg-accent',
                  )}
                >
                  <span className={cn('truncate text-sm', activeId === c.id && 'font-medium')} title={c.title}>
                    {c.title}
                  </span>
                  <span className="truncate text-[11px] text-muted-foreground">
                    {c.messageCount > 0 ? questionCount(c.messageCount) : 'Empty'}
                    {c.updatedAt ? ` · ${formatRelativeTime(c.updatedAt)}` : ''}
                  </span>
                </button>
                <div className="absolute top-1.5 right-1 flex items-center">
                  <Button
                    size="icon-xs"
                    variant="ghost"
                    className={ACTION}
                    aria-label={`Rename ${c.title}`}
                    onClick={() => {
                      setError(null)
                      setEditing(c.id)
                    }}
                  >
                    <Pencil aria-hidden="true" />
                  </Button>
                  <Button
                    size="icon-xs"
                    variant="ghost"
                    className={ACTION}
                    aria-label={`Delete ${c.title}`}
                    onClick={() => {
                      setError(null)
                      setConfirmDelete(c.id)
                    }}
                  >
                    <Trash2 aria-hidden="true" />
                  </Button>
                </div>
              </>
            )}
          </li>
        ))}
      </ul>

      {error && (
        <p role="alert" className="mx-2 mb-2 rounded-md border border-destructive/40 bg-destructive/10 px-2 py-1.5 text-xs text-destructive">
          {error}
        </p>
      )}
      {items.length > 0 && (
        <div className="border-t border-border p-2">
          {confirmClear ? (
            <div className="flex flex-col gap-2 text-xs" role="group" aria-label="Clear chat history?">
              <p className="text-destructive">
                Delete all {items.length} {items.length === 1 ? 'conversation' : 'conversations'} about this repository? This cannot be undone.
              </p>
              <div className="flex gap-2">
                <Button
                  size="xs"
                  variant="destructive"
                  disabled={pending === 'clear'}
                  onClick={() => void run('clear', onClear, () => setConfirmClear(false))}
                >
                  {pending === 'clear' && <Loader2 className="animate-spin" aria-hidden="true" />}
                  Delete all
                </Button>
                <Button size="xs" variant="ghost" onClick={() => setConfirmClear(false)}>
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <Button
              size="xs"
              variant="ghost"
              className="text-muted-foreground"
              onClick={() => {
                setError(null)
                setConfirmClear(true)
              }}
            >
              <Trash2 aria-hidden="true" />
              Clear history
            </Button>
          )}
        </div>
      )}
    </>
  )
}

function RenameRow({ initial, busy, onSave, onCancel }: { initial: string; busy: boolean; onSave: (title: string) => void; onCancel: () => void }) {
  const [value, setValue] = useState(initial)
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => {
    input.current?.focus()
    input.current?.select()
  }, [])

  const save = () => {
    const title = value.trim().replace(/\s+/g, ' ')
    if (!title || title === initial) return onCancel()
    onSave(title)
  }
  function handleKey(e: KeyboardEvent) {
    if (e.key === 'Enter') {
      e.preventDefault()
      save()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      onCancel()
    }
  }

  return (
    <div className="flex items-center gap-1 px-1 py-1">
      <input
        ref={input}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKey}
        maxLength={255}
        disabled={busy}
        aria-label="Conversation title"
        placeholder="Conversation title"
        className="h-7 min-w-0 flex-1 rounded-md border border-ring bg-background px-2 text-sm outline-none"
      />
      <Button size="icon-xs" variant="ghost" onClick={save} disabled={busy} aria-label="Save title">
        {busy ? <Loader2 className="animate-spin" aria-hidden="true" /> : <Check aria-hidden="true" />}
      </Button>
      <Button size="icon-xs" variant="ghost" onClick={onCancel} disabled={busy} aria-label="Cancel rename">
        <X aria-hidden="true" />
      </Button>
    </div>
  )
}
