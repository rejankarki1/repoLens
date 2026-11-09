import assert from 'node:assert/strict'
import { test } from 'node:test'
import { resumeIndexingAction } from './search-index-ui.ts'
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
