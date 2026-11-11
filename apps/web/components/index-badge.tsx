import { Loader2 } from 'lucide-react'
import { indexBadge, type IndexBadgeTone } from '@/lib/api/search-index-ui'
import type { ApiSearchIndex } from '@/lib/api/chat'
import { cn } from '@/lib/utils'

const TONE: Record<IndexBadgeTone, string> = {
  success: 'text-success bg-success/10',
  working: 'text-primary bg-primary/10',
  warning: 'text-warning bg-warning/10',
  muted: 'text-muted-foreground bg-muted',
}

/** Search readiness of a repository (semantic index state), from the status the caller already loaded. */
export function IndexBadge({ index, className }: { index: ApiSearchIndex | undefined; className?: string }) {
  const badge = indexBadge(index)
  if (!badge) return null
  return (
    <span title={badge.hint} className={cn('inline-flex h-5 items-center gap-1 rounded px-1.5 text-[11px] font-medium', TONE[badge.tone], className)}>
      {badge.tone === 'working' && <Loader2 className="size-3 animate-spin" aria-hidden="true" />}
      {badge.label}
    </span>
  )
}
