/**
 * Pure decision logic for the "Resume Indexing" action (Ask AI's chat panel): whether to show it, and in
 * what state, given the repository's current `ApiSearchIndex`. Kept separate from the component so the
 * status-transition rules are unit-testable without a browser. Authorization is not decided here: the
 * button only ever renders inside `ChatPanel`, which is already gated to `viewerAccess === 'member'`, and
 * the backend's own `member_write` dependency is the actual enforcement either way.
 */

import type { ApiSearchIndex } from './chat'

export type ResumeIndexingAction =
  | { visible: false }
  | { visible: true; disabled: boolean; label: string }

/**
 * `disabled` covers both "already running" (nothing to do - it is already resumed) and "not yet known"
 * (no status loaded yet, so starting a job would be a guess). The button never appears for `disabled`
 * (no provider key: starting a job could not help) or `ready` (nothing left to embed), or when there are
 * no chunks at all (ingest first, as the existing "no index yet" notice already says).
 */
export function resumeIndexingAction(index: ApiSearchIndex | undefined): ResumeIndexingAction {
  if (!index || index.chunks === 0 || index.semantic === 'disabled' || index.semantic === 'ready') {
    return { visible: false }
  }
  if (index.embedding_job_running) {
    return { visible: true, disabled: true, label: 'Indexing…' }
  }
  return { visible: true, disabled: false, label: index.semantic === 'partial' ? 'Resume indexing' : 'Build index' }
}
