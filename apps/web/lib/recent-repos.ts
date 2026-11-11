/** "Recent" means recently opened, not recently imported. Only repository ids and times are kept, in this browser. */

export const RECENT_KEY = 'repolens:recently-opened'
export type Opened = Record<string, number>

export function parseOpened(raw: string | null): Opened {
  if (!raw) return {}
  try {
    const value: unknown = JSON.parse(raw)
    if (typeof value !== 'object' || value === null || Array.isArray(value)) return {}
    return Object.fromEntries(Object.entries(value).filter(([, t]) => typeof t === 'number' && Number.isFinite(t))) as Opened
  } catch {
    return {}
  }
}

/** The stored map with `id` marked as opened now, trimmed to the `keep` most recent entries. */
export function withOpened(opened: Opened, id: string, now: number, keep = 30): Opened {
  const next = { ...opened, [id]: now }
  const newest = Object.entries(next).sort((a, b) => b[1] - a[1]).slice(0, keep)
  return Object.fromEntries(newest)
}

/** Opened repositories first (latest first), then the rest in their given order. */
export function sortByRecent<T extends { id: string }>(repositories: T[], opened: Opened): T[] {
  return repositories
    .map((repo, index) => ({ repo, index, at: opened[repo.id] ?? 0 }))
    .sort((a, b) => (a.at && b.at ? b.at - a.at : a.at ? -1 : b.at ? 1 : a.index - b.index))
    .map((entry) => entry.repo)
}
