import assert from 'node:assert/strict'
import { test } from 'node:test'
import { filterRepositories, isIngesting } from './repo-utils.ts'

const repos = [{ fullName: 'pallets/flask' }, { fullName: 'Octo/Hello-World' }, { fullName: 'pallets/click' }]

test('filtering matches owner or name, ignores case and surrounding spaces', () => {
  assert.deepEqual(filterRepositories(repos, ' PALLETS '), [repos[0], repos[2]])
  assert.deepEqual(filterRepositories(repos, 'hello'), [repos[1]])
  assert.deepEqual(filterRepositories(repos, 'nothing'), [])
})

test('an empty search keeps every repository in order', () => {
  assert.deepEqual(filterRepositories(repos, ''), repos)
  assert.deepEqual(filterRepositories(repos, '   '), repos)
})

test('only queued or running ingestions count as busy', () => {
  for (const status of ['queued', 'importing', 'indexing'] as const) assert.equal(isIngesting(status), true)
  for (const status of ['pending', 'completed', 'failed'] as const) assert.equal(isIngesting(status), false)
})
