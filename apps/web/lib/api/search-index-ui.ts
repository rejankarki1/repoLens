/**
 * Pure decision logic for the "Resume Indexing" action (Ask AI's chat panel): whether to show it, and in
 * what state, given the repository's current `ApiSearchIndex`. Kept separate from the component so the
 * status-transition rules are unit-testable without a browser. Authorization is not decided here: the
 * button only ever renders inside `ChatPanel`, which is already gated to `viewerAccess === 'member'`, and
 * the backend's own `member_write` dependency is the actual enforcement either way.
 */

import type { ApiSearchIndex } from './chat'

/** The one-line status shown above the question box. Every loaded state with chunks gets a line, including
 * `ready` - leaving `ready` blank made a finished index indistinguishable from a status that never loaded. */
export function describeIndex(index: ApiSearchIndex | undefined): string | null {
  if (!index || index.chunks === 0) return null
  switch (index.semantic) {
    case 'disabled':
      return 'Semantic search is not configured, so answers use keyword matching.'
    case 'not_built':
      return index.embedding_job_running
        ? 'Building the semantic index… Answers use keyword matching until it is ready.'
        : 'The semantic index has not been built yet. Answers use keyword matching, which works best with exact names.'
    case 'partial':
      return `Semantic index ${index.embedded_chunks} of ${index.chunks} chunks${index.embedding_job_running ? ' (indexing…)' : ''}. Questions still work and use keyword matching for the rest.`
    case 'ready':
      return `Semantic index ready (${index.chunks} chunks).`
    default:
      return null
  }
}

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
