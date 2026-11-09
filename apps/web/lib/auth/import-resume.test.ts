import assert from 'node:assert/strict'
import { test } from 'node:test'
import { authErrorMessage, pendingImport, resumePath } from './import-resume.ts'

test('the pasted URL survives the sign-in round trip', () => {
  const url = 'https://github.com/pallets/flask'
  const path = resumePath(url)
  assert.equal(path, '/dashboard?import=https%3A%2F%2Fgithub.com%2Fpallets%2Fflask')
  assert.equal(pendingImport(path.slice(path.indexOf('?'))), url)
})

test('nothing pending, or something absurd, is ignored', () => {
  assert.equal(pendingImport(''), null)
  assert.equal(pendingImport('?other=1'), null)
  assert.equal(pendingImport(`?import=${'a'.repeat(400)}`), null)
})

test('sign-in errors have fixed, friendly messages and unknown codes are not echoed', () => {
  assert.match(authErrorMessage('?auth_error=access_denied') ?? '', /cancelled/)
  assert.match(authErrorMessage('?auth_error=invalid_state') ?? '', /could not be verified/)
  assert.equal(authErrorMessage('?auth_error=<script>alert(1)</script>'), 'Sign-in failed. Please try again.')
  assert.equal(authErrorMessage(''), null)
})
