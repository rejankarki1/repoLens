import assert from 'node:assert/strict'
import { test } from 'node:test'
import { classifyFailure, describeFailure, isPermanentFailure, isTransientFailure, loadView } from './request-state.ts'

/** The shape `ApiError` has at runtime (an Error carrying a numeric `status`), without importing the fetch client. */
function apiError(status: number, message = `Request failed (${status})`): Error {
  return Object.assign(new Error(message), { status })
}

test('classifies API failures by status: network, auth, missing, throttled, server, request', () => {
  assert.equal(classifyFailure(apiError(0)), 'unreachable')
  assert.equal(classifyFailure(apiError(401)), 'unauthorized')
  assert.equal(classifyFailure(apiError(403)), 'forbidden')
  assert.equal(classifyFailure(apiError(404)), 'not_found')
  assert.equal(classifyFailure(apiError(408)), 'retry_later')
  assert.equal(classifyFailure(apiError(429)), 'retry_later')
  assert.equal(classifyFailure(apiError(500)), 'server')
  assert.equal(classifyFailure(apiError(502)), 'server')
  assert.equal(classifyFailure(apiError(503)), 'server')
  assert.equal(classifyFailure(apiError(422)), 'request')
  assert.equal(classifyFailure(new Error('boom')), 'unknown')
  assert.equal(classifyFailure(undefined), 'unknown')
})

test('a missing, forbidden or unauthenticated resource is permanent; network and 5xx failures are transient', () => {
  for (const status of [401, 403, 404, 400, 422]) {
    assert.equal(isPermanentFailure(apiError(status)), true, `status ${status} should be permanent`)
    assert.equal(isTransientFailure(apiError(status)), false)
  }
  for (const status of [0, 500, 502, 503, 504, 429]) {
    assert.equal(isPermanentFailure(apiError(status)), false, `status ${status} should not be permanent`)
    assert.equal(isTransientFailure(apiError(status)), true)
  }
  // An unexpected non-API error is neither: it is shown, but nobody promises a retry fixes it.
  assert.equal(isPermanentFailure(new TypeError('x is not a function')), false)
  assert.equal(isTransientFailure(new TypeError('x is not a function')), false)
})

test('loadView: the first fetch is loading, not an error', () => {
  assert.deepEqual(loadView({ data: undefined, error: undefined, isLoading: true }), { kind: 'loading' })
})

test('loadView: a disabled or not-yet-started query renders as loading rather than an unexplained error', () => {
  assert.deepEqual(loadView({ data: undefined, error: undefined, isLoading: false }), { kind: 'loading' })
})

test('loadView: cached data survives a transient refetch failure (network, 5xx) and is flagged as not refreshed', () => {
  const repo = { id: '7' }
  for (const status of [0, 500, 502, 503]) {
    assert.deepEqual(loadView({ data: repo, error: apiError(status), isLoading: false }), { kind: 'ready', data: repo, refreshFailed: true })
  }
})

test('loadView: a permanent failure replaces cached data (the repository was removed, the session ended)', () => {
  const repo = { id: '7' }
  const gone = loadView({ data: repo, error: apiError(404), isLoading: false })
  assert.equal(gone.kind, 'error')
  assert.equal(gone.kind === 'error' && gone.failure, 'not_found')
  assert.equal(gone.kind === 'error' && gone.transient, false)
  const signedOut = loadView({ data: repo, error: apiError(401), isLoading: false })
  assert.equal(signedOut.kind === 'error' && signedOut.failure, 'unauthorized')
})

test('loadView: with nothing cached, any failure is an error, and a transient one is marked retryable', () => {
  const down = loadView({ data: undefined, error: apiError(503), isLoading: false })
  assert.equal(down.kind, 'error')
  assert.equal(down.kind === 'error' && down.transient, true)
  const missing = loadView({ data: undefined, error: apiError(404), isLoading: false })
  assert.equal(missing.kind === 'error' && missing.transient, false)
})

test('loadView: a null result (the API said 404 on purpose, e.g. "no diagram yet") is data, not an error', () => {
  assert.deepEqual(loadView<null | { x: 1 }>({ data: null, error: undefined, isLoading: false }), { kind: 'ready', data: null, refreshFailed: false })
})

test('describeFailure: explains unreachable and failing servers in user terms; otherwise relays the API message', () => {
  assert.match(describeFailure(apiError(0, 'Could not reach the RepoLens API at http://x')), /could not be reached/)
  assert.match(describeFailure(apiError(502)), /not responding right now/)
  assert.match(describeFailure(apiError(429)), /try again in a moment/)
  assert.equal(describeFailure(apiError(404, 'Repository 9 not found')), 'Repository 9 not found')
  assert.equal(describeFailure('not an error', 'Fallback.'), 'Fallback.')
})
