'use client'

import { useMemo, useSyncExternalStore } from 'react'
import { parseOpened, RECENT_KEY, withOpened, type Opened } from '@/lib/recent-repos'

const EVENT = 'repolens:recently-opened'

function subscribe(onChange: () => void) {
  window.addEventListener('storage', onChange)
  window.addEventListener(EVENT, onChange)
  return () => {
    window.removeEventListener('storage', onChange)
    window.removeEventListener(EVENT, onChange)
  }
}

function read(): string | null {
  try {
    return window.localStorage.getItem(RECENT_KEY)
  } catch {
    return null // storage blocked: recents simply fall back to import order
  }
}

/** When each repository was last opened in this browser (empty until the first visit). */
export function useRecentlyOpened(): Opened {
  const raw = useSyncExternalStore(subscribe, read, () => null)
  return useMemo(() => parseOpened(raw), [raw])
}

export function markRepositoryOpened(id: string) {
  try {
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(withOpened(parseOpened(read()), id, Date.now())))
    window.dispatchEvent(new Event(EVENT))
  } catch {
    /* storage blocked */
  }
}

export function clearRecentlyOpened() {
  try {
    window.localStorage.removeItem(RECENT_KEY)
    window.dispatchEvent(new Event(EVENT))
  } catch {
    /* storage blocked */
  }
}
