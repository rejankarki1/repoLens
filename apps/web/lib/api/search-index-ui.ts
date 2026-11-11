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
      return 'Answers match on exact words and names, so use the names the code uses.'
    case 'not_built':
      return index.embedding_job_running
        ? 'Building the semantic index (matching by meaning)… Until it is ready, answers match on exact words.'
        : 'The semantic index is not built yet, so answers match on exact words and names. Build it to also match by meaning.'
    case 'partial':
      return `Semantic index covers ${index.embedded_chunks} of ${index.chunks} code chunks${index.embedding_job_running ? ' (indexing…)' : ''}. Questions already work; uncovered code is matched on exact words.`
    case 'ready':
      return `Semantic index ready (${index.chunks} code chunks): questions are matched by meaning, not just exact words.`
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

export type IndexBadgeTone = 'success' | 'working' | 'warning' | 'muted'

const KEYWORD_HINT = 'Questions are matched on exact words and names. Semantic search (matching by meaning) is not built for this repository.'

/** A short label for repository lists: how searchable the repository is right now. Null until the status is known. */
export function indexBadge(index: ApiSearchIndex | undefined): { label: string; tone: IndexBadgeTone; hint: string } | null {
  if (!index) return null
  if (index.chunks === 0) return { label: 'No code index', tone: 'warning', hint: 'No searchable code is stored yet. Re-analyze the repository to build it.' }
  switch (index.semantic) {
    case 'ready':
      return { label: 'Semantic index ready', tone: 'success', hint: 'Questions are matched by meaning as well as by exact words, so answers find more relevant code.' }
    case 'partial':
      return {
        label: `Index ${index.embedded_chunks}/${index.chunks}`,
        tone: index.embedding_job_running ? 'working' : 'warning',
        hint: 'The semantic index covers part of the code. Questions still work and use exact-word matching for the rest.',
      }
    case 'not_built':
      return index.embedding_job_running
        ? { label: 'Indexing…', tone: 'working', hint: 'Building the semantic index. Questions use exact-word matching until it is ready.' }
        : { label: 'Keyword search', tone: 'muted', hint: KEYWORD_HINT }
    case 'disabled':
      return { label: 'Keyword search', tone: 'muted', hint: KEYWORD_HINT }
    default:
      return null
  }
}
