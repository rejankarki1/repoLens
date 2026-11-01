import assert from 'node:assert/strict'
import { test } from 'node:test'
import { isSafeMethod, withAuth } from './csrf.ts'

test('state-changing requests carry the CSRF token and the cookie is always sent', () => {
  const post = withAuth({ method: 'POST', headers: { 'Content-Type': 'application/json' } }, 'tok')
  assert.equal(new Headers(post.headers).get('X-CSRF-Token'), 'tok')
  assert.equal(new Headers(post.headers).get('Content-Type'), 'application/json')
  assert.equal(post.credentials, 'include')
  for (const method of ['PATCH', 'delete', 'PUT']) assert.equal(new Headers(withAuth({ method }, 'tok').headers).get('X-CSRF-Token'), 'tok')
})

test('reads never send the token, and a missing token adds nothing', () => {
  assert.equal(new Headers(withAuth({ method: 'GET' }, 'tok').headers).get('X-CSRF-Token'), null)
  assert.equal(new Headers(withAuth(undefined, 'tok').headers).get('X-CSRF-Token'), null)
  assert.equal(new Headers(withAuth({ method: 'POST' }, null).headers).get('X-CSRF-Token'), null)
  assert.equal(withAuth(undefined, null).credentials, 'include')
  assert.equal(isSafeMethod(undefined), true)
  assert.equal(isSafeMethod('post'), false)
})
