/**
 * Cache keys and revalidation policy for the SWR hooks (`hooks.ts`). Pure, so the rules are unit-tested.
 *
 * Two kinds of resource live in the cache:
 *
 * - **Versioned, immutable.** The key names the exact version: the ingestion time of the stored snapshot
 *   (file list, file contents), or the commit and completion time of an analysis (dependency graph, node
 *   detail, AI diagram). A refetch of the same key can only return the same bytes, so revalidating it on
 *   window focus or because it is "stale" is a wasted request (on a cold-starting free-tier API, a slow
 *   one). The key *changing* is what refreshes these: a finished re-ingestion or a new analysis.
 * - **Mutable.** Repository status while ingesting, analysis and diagram status, the embedding index, the
 *   session, conversations, guest quotas: these change behind the user's back and keep SWR's defaults
 *   (plus explicit polling where the status is known to be moving).
 */
import type { Repository } from '@/lib/types'

/** Pass to `useSWR` for a versioned key. Reconnect revalidation stays on: it is also how a failed fetch recovers. */
export const IMMUTABLE_SWR_OPTIONS = { revalidateOnFocus: false, revalidateIfStale: false } as const

/**
 * Key of a repository-scoped resource of the stored snapshot. Present whenever a snapshot exists
 * (`indexedCommit`), even while a re-ingestion runs or after one failed: the backend keeps serving the last
 * good snapshot. `lastAnalyzedAt` changes on every successful ingestion, which refetches everything keyed by it.
 */
export function readyKey(repo: Pick<Repository, 'id' | 'indexedCommit' | 'lastAnalyzedAt'> | undefined, ...parts: string[]) {
  return repo?.indexedCommit ? [...parts, repo.id, repo.lastAnalyzedAt ?? ''] : null
}

interface StoredResult {
  has_result: boolean
}

/** Key of a stored analysis result: it changes exactly when a new analysis completes. */
export function resultKey(
  status: (StoredResult & { analyzed_commit_sha: string | null; analyzed_at: string | null }) | undefined,
  ...parts: string[]
) {
  return status?.has_result ? [...parts, status.analyzed_commit_sha ?? '', status.analyzed_at ?? ''] : null
}

/** Key of a stored AI diagram: it changes exactly when a new generation completes. */
export function diagramResultKey(
  status: (StoredResult & { generated_commit_sha: string | null; generated_at: string | null }) | undefined,
  ...parts: string[]
) {
  return status?.has_result ? [...parts, status.generated_commit_sha ?? '', status.generated_at ?? ''] : null
}
