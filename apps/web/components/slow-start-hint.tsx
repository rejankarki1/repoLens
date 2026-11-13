'use client'

import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'

/** How long a request has to stay pending before the hint appears: an ordinary load never shows it. */
export const SLOW_START_HINT_AFTER_MS = 4_000

/**
 * A one-line note shown next to a loading skeleton once a request has been pending for several seconds.
 *
 * The hosted API sleeps when idle (a free-tier instance) and takes a while to wake up on the first request, so
 * a visitor's first page can sit on a skeleton for tens of seconds with no explanation. Render this inside the
 * skeleton: it mounts and unmounts with it, so the timer runs only while that load is pending, nothing is
 * promised about *when* it finishes, and it never turns a slow request into an error.
 */
export function SlowStartHint({ className, afterMs = SLOW_START_HINT_AFTER_MS }: { className?: string; afterMs?: number }) {
  const [slow, setSlow] = useState(false)

  useEffect(() => {
    const id = setTimeout(() => setSlow(true), afterMs)
    return () => clearTimeout(id)
  }, [afterMs])

  if (!slow) return null
  return (
    <p role="status" className={cn('flex items-center gap-2 text-sm text-muted-foreground', className)}>
      <Loader2 className="size-4 shrink-0 animate-spin" aria-hidden="true" />
      Starting the server — this may take up to a minute.
    </p>
  )
}
