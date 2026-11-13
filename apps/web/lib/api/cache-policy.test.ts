import assert from 'node:assert/strict'
import { test } from 'node:test'
import { diagramResultKey, IMMUTABLE_SWR_OPTIONS, readyKey, resultKey } from './cache-policy.ts'

test('versioned data skips focus and stale revalidation but keeps reconnect (how a failed fetch recovers)', () => {
  assert.equal(IMMUTABLE_SWR_OPTIONS.revalidateOnFocus, false)
  assert.equal(IMMUTABLE_SWR_OPTIONS.revalidateIfStale, false)
  assert.equal('revalidateOnReconnect' in IMMUTABLE_SWR_OPTIONS, false)
  assert.equal('refreshInterval' in IMMUTABLE_SWR_OPTIONS, false)
})

test('snapshot keys exist only once a snapshot is stored, and change with every successful ingestion', () => {
  assert.equal(readyKey(undefined, 'files'), null)
  assert.equal(readyKey({ id: '4', indexedCommit: null, lastAnalyzedAt: null }, 'files'), null)
  const first = readyKey({ id: '4', indexedCommit: 'abc1234', lastAnalyzedAt: '2026-01-01T00:00:00Z' }, 'files')
  const again = readyKey({ id: '4', indexedCommit: 'abc1234', lastAnalyzedAt: '2026-01-02T00:00:00Z' }, 'files')
  assert.deepEqual(first, ['files', '4', '2026-01-01T00:00:00Z'])
  assert.notDeepEqual(first, again)
  // A file key carries the path, so two files of one snapshot never share an entry.
  assert.deepEqual(readyKey({ id: '4', indexedCommit: 'abc1234', lastAnalyzedAt: 't' }, 'file', 'src/a.ts'), ['file', 'src/a.ts', '4', 't'])
})

test('a re-ingestion in progress (or a failed one) keeps the last snapshot key: the backend still serves it', () => {
  const during = readyKey({ id: '4', indexedCommit: 'abc1234', lastAnalyzedAt: 't' }, 'files')
  assert.deepEqual(during, ['files', '4', 't'])
})

test('analysis and diagram result keys exist only with a stored result and change when a new one completes', () => {
  assert.equal(resultKey(undefined, 'architecture-graph', '4'), null)
  assert.equal(resultKey({ has_result: false, analyzed_commit_sha: null, analyzed_at: null }, 'architecture-graph', '4'), null)
  const a = resultKey({ has_result: true, analyzed_commit_sha: 'aaa', analyzed_at: 't1' }, 'architecture-graph', '4')
  const b = resultKey({ has_result: true, analyzed_commit_sha: 'aaa', analyzed_at: 't2' }, 'architecture-graph', '4')
  assert.deepEqual(a, ['architecture-graph', '4', 'aaa', 't1'])
  assert.notDeepEqual(a, b)

  assert.equal(diagramResultKey(undefined, 'architecture-diagram', '4'), null)
  const d = diagramResultKey({ has_result: true, generated_commit_sha: 'bbb', generated_at: 't1' }, 'architecture-diagram', '4')
  assert.deepEqual(d, ['architecture-diagram', '4', 'bbb', 't1'])
})
