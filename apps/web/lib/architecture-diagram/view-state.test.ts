import assert from 'node:assert/strict'
import { test } from 'node:test'
import { diagramView } from './view-state.ts'
import type { DiagramResponse, DiagramStatus } from '../api/architecture-diagram.ts'
import type { ArchitectureDiagram } from './types.ts'

function status(over: Partial<DiagramStatus> = {}): DiagramStatus {
  return {
    repository_id: 1,
    status: 'none',
    freshness: 'none',
    current_commit_sha: 'abc1234',
    generated_commit_sha: null,
    target_commit_sha: null,
    generated_at: null,
    requested_at: null,
    error: null,
    has_result: false,
    model: null,
    ...over,
  }
}

const stored = status({ status: 'completed', freshness: 'current', has_result: true, generated_commit_sha: 'abc1234', generated_at: '2026-01-01T00:00:00Z' })

const diagram = { components: [], relationships: [], groups: [], warnings: [] } as unknown as ArchitectureDiagram
const response: DiagramResponse = { status: stored, diagram }

function apiError(code: number, message = `Request failed (${code})`): Error {
  return Object.assign(new Error(message), { status: code })
}

const idle = { data: undefined, error: undefined, isLoading: false }
const loading = { data: undefined, error: undefined, isLoading: true }

test('status still loading: loading', () => {
  assert.deepEqual(diagramView(loading, idle), { kind: 'loading' })
})

test('status says a diagram exists but it has not arrived yet: loading, NOT "no diagram yet"', () => {
  assert.deepEqual(diagramView({ data: stored, error: undefined, isLoading: false }, loading), { kind: 'loading' })
})

test('status loaded and the diagram loaded: the diagram', () => {
  const view = diagramView({ data: stored, error: undefined, isLoading: false }, { data: response, error: undefined, isLoading: false })
  assert.equal(view.kind, 'diagram')
  assert.equal(view.kind === 'diagram' && view.refreshFailed, false)
})

test('a failed status poll keeps the diagram on screen (marked as not refreshed) instead of an error', () => {
  for (const code of [0, 502, 503]) {
    const view = diagramView({ data: stored, error: apiError(code), isLoading: false }, { data: response, error: undefined, isLoading: false })
    assert.equal(view.kind, 'diagram', `status ${code}`)
    assert.equal(view.kind === 'diagram' && view.refreshFailed, true)
  }
})

test('a failed status poll with nothing cached is a status error; network/server failures are retryable', () => {
  const down = diagramView({ data: undefined, error: apiError(503), isLoading: false }, idle)
  assert.equal(down.kind, 'status-error')
  assert.equal(down.kind === 'status-error' && down.retryable, true)
  const gone = diagramView({ data: undefined, error: apiError(404, 'Repository 1 not found'), isLoading: false }, idle)
  assert.deepEqual(gone, { kind: 'status-error', message: 'Repository 1 not found', retryable: false })
})

test('the session ending (401) replaces a cached diagram: that is not a blip to paper over', () => {
  const view = diagramView({ data: stored, error: apiError(401, 'Sign in'), isLoading: false }, { data: response, error: undefined, isLoading: false })
  assert.equal(view.kind, 'status-error')
})

test('status promises a diagram but fetching it failed with nothing cached: a diagram error, not "no diagram yet"', () => {
  const view = diagramView({ data: stored, error: undefined, isLoading: false }, { data: undefined, error: apiError(500), isLoading: false })
  assert.equal(view.kind, 'diagram-error')
  assert.equal(view.kind === 'diagram-error' && view.retryable, true)
})

test('status promises a diagram but the API answered 404 for it: falls through to the plain states', () => {
  const view = diagramView({ data: stored, error: undefined, isLoading: false }, { data: null, error: undefined, isLoading: false })
  assert.equal(view.kind, 'none')
})

test('queued or running with no earlier diagram: pending; failed with none: failed; nothing: none', () => {
  assert.equal(diagramView({ data: status({ status: 'queued' }), error: undefined, isLoading: false }, idle).kind, 'pending')
  assert.equal(diagramView({ data: status({ status: 'running' }), error: undefined, isLoading: false }, idle).kind, 'pending')
  assert.equal(diagramView({ data: status({ status: 'failed', error: 'boom' }), error: undefined, isLoading: false }, idle).kind, 'failed')
  assert.equal(diagramView({ data: status(), error: undefined, isLoading: false }, idle).kind, 'none')
})

test('regenerating while an older diagram is cached: still the diagram (the banner explains), not pending', () => {
  const regenerating = status({ status: 'running', freshness: 'stale', has_result: true, generated_commit_sha: 'old0000' })
  const view = diagramView({ data: regenerating, error: undefined, isLoading: false }, { data: response, error: undefined, isLoading: false })
  assert.equal(view.kind, 'diagram')
})
