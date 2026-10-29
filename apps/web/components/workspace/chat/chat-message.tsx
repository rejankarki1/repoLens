'use client'

import Link from 'next/link'
import { memo, useMemo } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { AlertCircle, FileCode2, OctagonX, RotateCcw, Sparkles, TriangleAlert } from 'lucide-react'
import { CodeBlock, formatLineRange } from '@/components/workspace/code-viewer'
import { Button } from '@/components/ui/button'
import { describeChatError, type ChatTurnMessage, type SourceCitation } from '@/lib/api/chat'
import { languageFromFence } from '@/lib/highlight'
import { cn } from '@/lib/utils'

const CITE_SCHEME = 'cite:'
// \u200b-\u200d, \u2060 and \ufeff are zero-width characters models sometimes put inside a marker.
const GAP = '[\\s\\u200b-\\u200d\\u2060\\ufeff]*'
// Brackets may be ASCII [ ] or the full-width \u3010 \u3011 / \uff3b \uff3d some models emit; the backend normalizes them on save.
const MARKER = new RegExp(`[\\[\\u3010\\uff3b]${GAP}(S\\d+(?:${GAP}[,;]${GAP}S\\d+)*)${GAP}[\\]\\u3011\\uff3d]`, 'g')
const CODE_SPANS = /(```[\s\S]*?```|`[^`\n]*`)/

/**
 * Turns `[S1]` / `[S1, S2]` into links with a private `cite:` scheme, but only for markers that match a
 * source the backend returned. Anything else is dropped, so the UI never shows a citation the server
 * did not validate. Fenced and inline code is left untouched.
 */
export function linkCitations(text: string, known: ReadonlySet<string>): string {
  return text
    .split(CODE_SPANS)
    .map((part, i) =>
      i % 2 === 1
        ? part
        : part.replace(MARKER, (_, group: string) => {
            const links = (group.match(/S\d+/g) ?? []).filter((id) => known.has(id)).map((id) => `[${id}](${CITE_SCHEME}${id})`)
            return links.join(' ')
          }),
    )
    .join('')
}

export function citationHref(repoId: string, c: SourceCitation): string {
  return `/repos/${repoId}?tab=code&file=${encodeURIComponent(c.path)}&lines=${formatLineRange(c.startLine, c.endLine)}`
}

function describeCitation(c: SourceCitation, stale: boolean): string {
  const where = `${c.path}, lines ${formatLineRange(c.startLine, c.endLine)}`
  const symbol = c.symbolName ? ` (${c.symbolName})` : ''
  return stale ? `${where}${symbol}. From an earlier snapshot; the lines may have moved.` : `${where}${symbol}`
}

/** Short pill used inline in the answer: the marker, e.g. "S2". */
function InlineCitation({ repoId, citation, stale }: { repoId: string; citation: SourceCitation; stale: boolean }) {
  return (
    <Link
      href={citationHref(repoId, citation)}
      title={describeCitation(citation, stale)}
      aria-label={`Source ${citation.id}: ${describeCitation(citation, stale)}`}
      className={cn(
        'mx-0.5 inline-flex h-[1.15rem] min-w-[1.6rem] items-center justify-center rounded border px-1 align-baseline font-mono text-[0.7rem] leading-none no-underline transition-colors',
        stale
          ? 'border-warning/50 bg-warning/10 text-warning hover:bg-warning/20'
          : 'border-primary/30 bg-primary/10 text-primary hover:bg-primary/20',
      )}
    >
      {citation.id}
    </Link>
  )
}

export function CitationChip({ repoId, citation, stale }: { repoId: string; citation: SourceCitation; stale: boolean }) {
  const range = formatLineRange(citation.startLine, citation.endLine)
  return (
    <Link
      href={citationHref(repoId, citation)}
      title={describeCitation(citation, stale)}
      className={cn(
        'inline-flex max-w-full items-center gap-1.5 rounded-md border bg-muted/50 px-2 py-1 font-mono text-xs transition-colors hover:bg-accent',
        stale ? 'border-warning/50' : 'border-border hover:border-primary/40',
      )}
    >
      <span className="shrink-0 rounded bg-background px-1 text-[0.65rem] text-primary">{citation.id}</span>
      <FileCode2 className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
      <span className="truncate">{citation.path}</span>
      <span className="shrink-0 text-muted-foreground">:{range}</span>
    </Link>
  )
}

interface MessageProps {
  message: ChatTurnMessage
  repoId: string
  /** Short commit of the repository's current snapshot; citations from another commit are flagged */
  currentCommit: string | null
  /** The failed question's retry, offered only on the latest failed answer */
  onRetry?: () => void
}

function isStale(c: SourceCitation, currentCommit: string | null) {
  return !!currentCommit && !!c.commitSha && !c.commitSha.startsWith(currentCommit)
}

function AnswerBody({ message, repoId, currentCommit }: Omit<MessageProps, 'onRetry'>) {
  // While streaming, markers resolve against the retrieved sources; afterwards, against the saved citations.
  const sources = message.status === 'complete' ? message.citations : (message.retrieval?.sources ?? message.citations)
  const byId = useMemo(() => new Map(sources.map((c) => [c.id, c])), [sources])
  const text = useMemo(() => linkCitations(message.content, new Set(byId.keys())), [message.content, byId])

  const components = useMemo<Components>(
    () => ({
      pre: ({ children }) => <>{children}</>,
      code: ({ className, children }) => {
        const lang = /language-(\w+)/.exec(className ?? '')?.[1]
        const code = String(children)
        if (!lang && !code.includes('\n')) {
          return <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[0.85em] break-words">{children}</code>
        }
        return <CodeBlock content={code.replace(/\n$/, '')} language={languageFromFence(lang)} label={lang} />
      },
      a: ({ href, children }) => {
        if (href?.startsWith(CITE_SCHEME)) {
          const citation = byId.get(href.slice(CITE_SCHEME.length))
          return citation ? <InlineCitation repoId={repoId} citation={citation} stale={isStale(citation, currentCommit)} /> : null
        }
        // Only absolute web links are clickable; anything else renders as plain text.
        if (href && /^https?:\/\//i.test(href)) {
          return (
            <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="text-primary underline underline-offset-2">
              {children}
            </a>
          )
        }
        return <span>{children}</span>
      },
      img: () => null,
      table: ({ children }) => (
        <div className="my-2 overflow-x-auto">
          <table className="w-full border-collapse text-left text-xs [&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:border-border [&_th]:bg-muted/50 [&_th]:px-2 [&_th]:py-1">{children}</table>
        </div>
      ),
    }),
    [byId, repoId, currentCommit],
  )

  return (
    <div className="flex flex-col gap-1 break-words [&_h1]:mt-3 [&_h1]:text-base [&_h1]:font-semibold [&_h2]:mt-3 [&_h2]:text-[0.95rem] [&_h2]:font-semibold [&_h3]:mt-2 [&_h3]:font-semibold [&_li]:my-0.5 [&_ol]:ml-5 [&_ol]:list-decimal [&_p]:my-1.5 [&_ul]:ml-5 [&_ul]:list-disc">
      {/* No rehype-raw: HTML in the model's output is shown as text, never parsed. */}
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components} urlTransform={(url) => url}>
        {text}
      </ReactMarkdown>
    </div>
  )
}

function ChatMessageViewBase({ message, repoId, currentCommit, onRetry }: MessageProps) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <p className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-3.5 py-2 text-sm break-words whitespace-pre-wrap text-primary-foreground">
          {message.content}
        </p>
      </div>
    )
  }

  const { status, retrieval } = message
  const retrievalNotes = [...(retrieval?.warnings ?? []), ...(message.warnings ?? []).filter((w) => !retrieval?.warnings.includes(w))]
  const keywordOnly = retrieval?.mode === 'keyword'

  return (
    <div className="flex gap-3">
      <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md bg-muted" aria-hidden="true">
        <Sparkles className="size-4 text-primary" />
      </span>
      <div className="min-w-0 flex-1 text-sm leading-relaxed">
        {status === 'searching' ? (
          <p className="flex items-center gap-2 py-1 text-muted-foreground" role="status">
            <Dots />
            Searching the code…
          </p>
        ) : (
          <>
            {message.content && <AnswerBody message={message} repoId={repoId} currentCommit={currentCommit} />}
            {status === 'streaming' && (
              <p className="mt-1 flex items-center gap-2 text-xs text-muted-foreground" role="status">
                <Dots />
                {message.content ? 'Writing…' : 'Reading the matching code…'}
              </p>
            )}
          </>
        )}

        {status === 'failed' && message.error && (
          <div role="alert" className="mt-2 flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-destructive">
            <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <div className="flex min-w-0 flex-1 flex-col gap-2">
              <p>{describeChatError(message.error)}</p>
              <p className="text-xs opacity-80">This answer was not saved.</p>
              {onRetry && (
                <Button size="xs" variant="outline" className="self-start" onClick={onRetry}>
                  <RotateCcw aria-hidden="true" />
                  Try again
                </Button>
              )}
            </div>
          </div>
        )}

        {status === 'stopped' && (
          <div className="mt-2 flex items-start gap-2 rounded-md border border-border bg-muted/40 px-3 py-2 text-muted-foreground">
            <OctagonX className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <div className="flex flex-col gap-2">
              <p>Stopped. This answer was not saved.</p>
              {onRetry && (
                <Button size="xs" variant="outline" className="self-start" onClick={onRetry}>
                  <RotateCcw aria-hidden="true" />
                  Ask again
                </Button>
              )}
            </div>
          </div>
        )}

        {status === 'complete' && message.noEvidence && (
          <p className="mt-2 text-xs text-muted-foreground">No matching code was found, so no AI answer was generated.</p>
        )}

        {(status === 'complete' || status === 'streaming') && (keywordOnly || retrievalNotes.length > 0) && (
          <ul className="mt-2 flex flex-col gap-1 text-xs text-muted-foreground">
            {keywordOnly && !retrievalNotes.some((n) => /keyword/i.test(n)) && (
              <li className="flex items-start gap-1.5">
                <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
                Matched by keywords only; the semantic index was not used.
              </li>
            )}
            {retrievalNotes.map((note) => (
              <li key={note} className="flex items-start gap-1.5">
                <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
                {note}
              </li>
            ))}
          </ul>
        )}

        {status === 'complete' && message.citations.length > 0 && (
          <div className="mt-3 flex flex-col gap-1.5">
            <p className="text-xs text-muted-foreground">Sources</p>
            <div className="flex flex-wrap gap-2">
              {message.citations.map((c) => (
                <CitationChip key={c.id} repoId={repoId} citation={c} stale={isStale(c, currentCommit)} />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function Dots() {
  return (
    <span className="flex gap-1" aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <span key={i} className="size-1.5 animate-pulse rounded-full bg-muted-foreground" style={{ animationDelay: `${i * 150}ms` }} />
      ))}
    </span>
  )
}

export const ChatMessageView = memo(ChatMessageViewBase)
