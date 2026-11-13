/**
 * How a component should read an SWR query: pure helpers so the decision "show the data, a skeleton, or an
 * error" is the same everywhere and can be unit-tested without React.
 *
 * SWR keeps the last good `data` when a revalidation fails. A component that checks `error` first throws
 * that data away on every blip (a dropped connection, a cold-starting server, a 502 from the proxy) and
 * unmounts whatever was shown — including an Ask AI answer still streaming. The rule here: cached data
 * survives a *transient* failure; only a *permanent* one (the resource is gone, or the viewer may not see
 * it) replaces what is on screen.
 */

/** The subset of an SWR response these helpers read (no dependency on `swr` itself, so tests stay pure). */
export interface QueryState<T> {
  data: T | undefined
  error: unknown
  isLoading: boolean
}

export type FailureKind =
  /** The request never reached the API (DNS, connection refused, the proxy gave up, offline). */
  | 'unreachable'
  /** 401: a session is needed. */
  | 'unauthorized'
  /** 403: signed in, but not allowed. */
  | 'forbidden'
  /** 404: the resource does not exist (or is not visible to this viewer, which the API reports the same way). */
  | 'not_found'
  /** 408 or 429: the server asked to try again later. */
  | 'retry_later'
  /** 5xx: the server failed; a retry may well succeed (a cold start, a deploy, a hiccup). */
  | 'server'
  /** Any other 4xx: the request itself was wrong; retrying will not help. */
  | 'request'
  /** Not an API failure at all (a bug, a parse error). */
  | 'unknown'

/** `ApiError.status` without importing the client (which pulls in `fetch`-only modules). 0 = never reached the API. */
function statusOf(err: unknown): number | null {
  if (err instanceof Error && 'status' in err && typeof err.status === 'number') return err.status
  return null
}

export function classifyFailure(err: unknown): FailureKind {
  const status = statusOf(err)
  if (status === null) return 'unknown'
  if (status === 0) return 'unreachable'
  if (status === 401) return 'unauthorized'
  if (status === 403) return 'forbidden'
  if (status === 404) return 'not_found'
  if (status === 408 || status === 429) return 'retry_later'
  if (status >= 500) return 'server'
  if (status >= 400) return 'request'
  return 'unknown'
}

/** A failure the next retry cannot fix: cached data must not hide it (the repository was removed, the session expired). */
export function isPermanentFailure(err: unknown): boolean {
  const kind = classifyFailure(err)
  return kind === 'unauthorized' || kind === 'forbidden' || kind === 'not_found' || kind === 'request'
}

/** The opposite: the API was unreachable or failed on its side; whatever was loaded before is still the best picture. */
export function isTransientFailure(err: unknown): boolean {
  const kind = classifyFailure(err)
  return kind === 'unreachable' || kind === 'server' || kind === 'retry_later'
}

export type LoadView<T> =
  | { kind: 'loading' }
  /** Nothing usable is cached and the last request failed. `transient` says whether "Try again" makes sense. */
  | { kind: 'error'; error: unknown; failure: FailureKind; transient: boolean }
  /** Data to show. `refreshFailed` is true when the latest revalidation failed but the data is still good. */
  | { kind: 'ready'; data: T; refreshFailed: boolean }

/**
 * Resolves a query into exactly one of: show a skeleton, show an error, or show the data. Cached data wins over
 * a transient failure; a permanent failure wins over cached data; loading is only the very first fetch (SWR's
 * `isLoading`), so a background revalidation never flips a loaded view back to a skeleton.
 */
export function loadView<T>(query: QueryState<T>): LoadView<T> {
  const { data, error, isLoading } = query
  if (error && (data === undefined || isPermanentFailure(error))) {
    return { kind: 'error', error, failure: classifyFailure(error), transient: isTransientFailure(error) }
  }
  if (data !== undefined) return { kind: 'ready', data, refreshFailed: !!error }
  if (isLoading) return { kind: 'loading' }
  // No data, no error, not loading: the key is disabled or the fetch has not started. Treat as loading: the
  // caller renders its skeleton rather than an error nothing explains.
  return { kind: 'loading' }
}

/** A user-facing sentence for a failed request, from the message the API sent when it is one. */
export function describeFailure(err: unknown, fallback = 'Something went wrong.'): string {
  const kind = classifyFailure(err)
  if (kind === 'unreachable') return 'The RepoLens API could not be reached. Check your connection and try again.'
  if (kind === 'server') return 'The RepoLens API is not responding right now. It may be starting up; try again in a moment.'
  if (kind === 'retry_later') return 'The RepoLens API asked to try again in a moment.'
  return err instanceof Error && err.message ? err.message : fallback
}
