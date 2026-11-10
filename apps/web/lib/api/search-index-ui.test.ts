import assert from 'node:assert/strict'
import { test } from 'node:test'
import { describeIndex, indexBadge, resumeIndexingAction } from './search-index-ui.ts'
import type { ApiSearchIndex } from './chat.ts'

function index(over: Partial<ApiSearchIndex>): ApiSearchIndex {
  return { chunks: 200, embedded_chunks: 0, semantic: 'partial', embedding_job_running: false, ...over }
}

test('no status loaded yet: hidden', () => {
  assert.deepEqual(resumeIndexingAction(undefined), { visible: false })
})

test('no code chunks at all: hidden (ingest first, not an indexing problem)', () => {
  assert.deepEqual(resumeIndexingAction(index({ chunks: 0, semantic: 'not_built' })), { visible: false })
})

test('semantic search disabled (no provider key): hidden - starting a job could not help', () => {
  assert.deepEqual(resumeIndexingAction(index({ semantic: 'disabled' })), { visible: false })
})

test('fully indexed: hidden - nothing left to embed', () => {
  assert.deepEqual(resumeIndexingAction(index({ semantic: 'ready', embedded_chunks: 200 })), { visible: false })
})

test('partially indexed (the Wanderlust 64/200 case): visible, enabled, labelled to resume', () => {
  assert.deepEqual(
    resumeIndexingAction(index({ semantic: 'partial', embedded_chunks: 64, chunks: 200 })),
    { visible: true, disabled: false, label: 'Resume indexing' },
  )
})

test('never built yet: visible, enabled, labelled to build (not "resume" - nothing ran before)', () => {
  assert.deepEqual(
    resumeIndexingAction(index({ semantic: 'not_built', embedded_chunks: 0 })),
    { visible: true, disabled: false, label: 'Build index' },
  )
})

test('a job is already running: visible but disabled, so a second click cannot queue a duplicate', () => {
  assert.deepEqual(
    resumeIndexingAction(index({ semantic: 'partial', embedding_job_running: true })),
    { visible: true, disabled: true, label: 'Indexing…' },
  )
})

test('a job is running even while technically "not_built" yet (first attempt in flight): still disabled', () => {
  assert.deepEqual(
    resumeIndexingAction(index({ semantic: 'not_built', embedding_job_running: true })),
    { visible: true, disabled: true, label: 'Indexing…' },
  )
})

// --- describeIndex: the "nothing appears above the question box" regression -----------------------------

test('regression: a fully indexed repository still shows a status line (it used to render nothing at all)', () => {
  assert.equal(describeIndex(index({ semantic: 'ready', embedded_chunks: 200, chunks: 200 })), 'Semantic index ready (200 chunks).')
})

test('regression: every loaded state with chunks shows a status line, a button, or both - never nothing', () => {
  for (const semantic of ['disabled', 'not_built', 'partial', 'ready'] as const) {
    for (const embedding_job_running of [false, true]) {
      const loaded = index({ semantic, embedding_job_running, embedded_chunks: semantic === 'ready' ? 200 : 64 })
      assert.ok(
        describeIndex(loaded) !== null || resumeIndexingAction(loaded).visible,
        `semantic=${semantic} running=${embedding_job_running} rendered nothing`,
      )
    }
  }
})

test('partial status shows embedded/total progress, and says when a job is running', () => {
  assert.match(describeIndex(index({ semantic: 'partial', embedded_chunks: 64 })) ?? '', /64 of 200 chunks\. /)
  assert.match(describeIndex(index({ semantic: 'partial', embedded_chunks: 64, embedding_job_running: true })) ?? '', /64 of 200 chunks \(indexing…\)/)
})

test('a first build in progress is described as building, not as "not built yet"', () => {
  assert.match(describeIndex(index({ semantic: 'not_built', embedding_job_running: true })) ?? '', /^Building the semantic index/)
})

test('nothing to describe before the status loads or when there are no chunks', () => {
  assert.equal(describeIndex(undefined), null)
  assert.equal(describeIndex(index({ chunks: 0, semantic: 'not_built' })), null)
})

test('index badge: unknown until the status loads, then one short label per state', () => {
  assert.equal(indexBadge(undefined), null)
  assert.deepEqual(indexBadge(index({ chunks: 0, semantic: 'not_built' })), { label: 'No code index', tone: 'warning' })
  assert.deepEqual(indexBadge(index({ semantic: 'ready', embedded_chunks: 200 })), { label: 'Semantic index ready', tone: 'success' })
  assert.deepEqual(indexBadge(index({ semantic: 'disabled' })), { label: 'Keyword search', tone: 'muted' })
  assert.deepEqual(indexBadge(index({ semantic: 'not_built' })), { label: 'Keyword search', tone: 'muted' })
})

test('index badge: a partial or running index says how far it is and whether it is still working', () => {
  assert.deepEqual(indexBadge(index({ semantic: 'partial', embedded_chunks: 80 })), { label: 'Index 80/200', tone: 'warning' })
  assert.deepEqual(indexBadge(index({ semantic: 'partial', embedded_chunks: 80, embedding_job_running: true })), { label: 'Index 80/200', tone: 'working' })
  assert.deepEqual(indexBadge(index({ semantic: 'not_built', embedding_job_running: true })), { label: 'Indexing…', tone: 'working' })
})
