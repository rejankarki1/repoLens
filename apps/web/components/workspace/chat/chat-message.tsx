'use client'

import Link from 'next/link'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { FileCode2, Sparkles } from 'lucide-react'
import { CodeBlock } from '@/components/workspace/code-viewer'
import { languageFromFence } from '@/lib/highlight'
import { formatLineRange } from '@/components/workspace/code-viewer'
import type { ChatMessage } from '@/lib/types'

export function CitationChip({ repoId, path, startLine, endLine }: { repoId: string; path: string; startLine: number; endLine: number }) {
  const range = formatLineRange(startLine, endLine)
  return (
    <Link
      href={`/repos/${repoId}?tab=code&file=${encodeURIComponent(path)}&lines=${range}`}
      className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-border bg-muted/50 px-2 py-1 font-mono text-xs transition-colors hover:border-primary/40 hover:bg-accent"
    >
      <FileCode2 className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
      <span className="truncate">{path}</span>
      <span className="shrink-0 text-muted-foreground">:{range}</span>
    </Link>
  )
}

export function ChatMessageView({ message, repoId }: { message: ChatMessage; repoId: string }) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <p className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-3.5 py-2 text-sm text-primary-foreground">
          {message.content}
        </p>
      </div>
    )
  }

  return (
    <div className="flex gap-3">
      <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md bg-muted" aria-hidden="true">
        <Sparkles className="size-4 text-primary" />
      </span>
      <div className="min-w-0 flex-1 text-sm leading-relaxed">
        {message.status === 'thinking' ? (
          <p className="flex items-center gap-2 py-1 text-muted-foreground" role="status">
            <span className="flex gap-1" aria-hidden="true">
              {[0, 1, 2].map((i) => (
                <span key={i} className="size-1.5 animate-pulse rounded-full bg-muted-foreground" style={{ animationDelay: `${i * 150}ms` }} />
              ))}
            </span>
            Searching sample files…
          </p>
        ) : (
          <div className="flex flex-col gap-1 [&_li]:my-0.5 [&_ol]:ml-5 [&_ol]:list-decimal [&_p]:my-1.5 [&_ul]:ml-5 [&_ul]:list-disc">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                pre: ({ children }) => <>{children}</>,
                code: ({ className, children }) => {
                  const lang = /language-(\w+)/.exec(className ?? '')?.[1]
                  const text = String(children)
                  if (!lang && !text.includes('\n')) {
                    return <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[0.85em]">{children}</code>
                  }
                  return <CodeBlock content={text} language={languageFromFence(lang)} label={lang} />
                },
              }}
            >
              {message.content}
            </ReactMarkdown>
          </div>
        )}
        {message.status === 'complete' && message.citations.length > 0 && (
          <div className="mt-3 flex flex-col gap-1.5">
            <p className="text-xs text-muted-foreground">Sources</p>
            <div className="flex flex-wrap gap-2">
              {message.citations.map((c) => (
                <CitationChip key={`${c.path}:${c.startLine}`} repoId={repoId} {...c} />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
