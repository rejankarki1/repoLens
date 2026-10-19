'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { Check, Copy } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { highlightLines, TOKEN_CLASS } from '@/lib/highlight'
import type { CodeLanguage } from '@/lib/types'
import { cn } from '@/lib/utils'

export interface LineRange {
  start: number
  end: number
}

/** Parses "12-20" or "12" from a URL query value. */
export function parseLineRange(value: string | null | undefined): LineRange | null {
  if (!value) return null
  const match = /^(\d+)(?:-(\d+))?$/.exec(value)
  if (!match) return null
  const start = Number(match[1])
  const end = match[2] ? Number(match[2]) : start
  return start > 0 && end >= start ? { start, end } : null
}

export function formatLineRange(start: number, end: number) {
  return start === end ? `${start}` : `${start}-${end}`
}

function CopyButton({ text, className }: { text: string; className?: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <Button
      variant="ghost"
      size="icon-xs"
      className={className}
      aria-label={copied ? 'Copied' : 'Copy code'}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text)
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        } catch {
          /* clipboard unavailable */
        }
      }}
    >
      {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
    </Button>
  )
}

interface CodeViewerProps {
  content: string
  language: CodeLanguage
  highlight?: LineRange | null
  className?: string
}

/** Full-height source view with line numbers and an optional highlighted range. */
export function CodeViewer({ content, language, highlight, className }: CodeViewerProps) {
  const lines = useMemo(() => highlightLines(content, language), [content, language])
  const rangeRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    rangeRef.current?.scrollIntoView({ block: 'center' })
  }, [content, highlight?.start, highlight?.end])

  const gutter = String(lines.length).length

  return (
    <div className={cn('relative min-w-0 overflow-auto bg-card font-mono text-[13px] leading-6', className)}>
      <CopyButton text={content} className="absolute top-2 right-3 z-10 bg-card/80 backdrop-blur" />
      <pre className="min-w-max py-3">
        <code>
          {lines.map((tokens, i) => {
            const n = i + 1
            const active = highlight && n >= highlight.start && n <= highlight.end
            return (
              <div
                key={n}
                ref={highlight && n === highlight.start ? rangeRef : undefined}
                className={cn('flex px-4', active && 'bg-primary/10 shadow-[inset_2px_0_0_var(--primary)]')}
              >
                <span
                  className="mr-4 shrink-0 text-right text-muted-foreground/60 select-none tabular-nums"
                  style={{ minWidth: `${gutter}ch` }}
                  aria-hidden="true"
                >
                  {n}
                </span>
                <span className="whitespace-pre">
                  {tokens.length === 0
                    ? '​'
                    : tokens.map((t, j) => (
                        <span key={j} className={TOKEN_CLASS[t.type]}>
                          {t.value}
                        </span>
                      ))}
                </span>
              </div>
            )
          })}
        </code>
      </pre>
    </div>
  )
}

/** Compact block for code snippets inside chat answers. */
export function CodeBlock({ content, language, label }: { content: string; language: CodeLanguage; label?: string }) {
  const lines = useMemo(() => highlightLines(content.replace(/\n$/, ''), language), [content, language])
  return (
    <div className="my-3 overflow-hidden rounded-md border border-border bg-muted/40">
      <div className="flex h-8 items-center justify-between border-b border-border pr-1 pl-3 text-xs text-muted-foreground">
        <span className="font-mono">{label ?? language}</span>
        <CopyButton text={content} />
      </div>
      <pre className="overflow-x-auto p-3 font-mono text-[13px] leading-6">
        <code>
          {lines.map((tokens, i) => (
            <div key={i} className="whitespace-pre">
              {tokens.length === 0
                ? '​'
                : tokens.map((t, j) => (
                    <span key={j} className={TOKEN_CLASS[t.type]}>
                      {t.value}
                    </span>
                  ))}
            </div>
          ))}
        </code>
      </pre>
    </div>
  )
}
