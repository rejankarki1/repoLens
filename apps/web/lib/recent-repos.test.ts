import assert from 'node:assert/strict'
import { test } from 'node:test'
import { parseOpened, sortByRecent, withOpened } from './recent-repos.ts'

const repos = [{ id: '5' }, { id: '4' }, { id: '3' }, { id: '2' }]

test('nothing opened yet keeps the import order', () => {
  assert.deepEqual(sortByRecent(repos, {}), repos)
})

test('opened repositories come first, latest first; the rest keep their order', () => {
  assert.deepEqual(sortByRecent(repos, { '3': 100, '2': 200 }).map((r) => r.id), ['2', '3', '5', '4'])
})

test('opened ids that are no longer listed are ignored', () => {
  assert.deepEqual(sortByRecent(repos, { '99': 500 }), repos)
})

test('marking a repository opened updates it and keeps only the newest entries', () => {
  const opened = withOpened({ a: 1, b: 2, c: 3 }, 'a', 10, 2)
  assert.deepEqual(opened, { a: 10, c: 3 })
})

test('stored data that is missing, malformed or the wrong shape reads as empty', () => {
  assert.deepEqual(parseOpened(null), {})
  assert.deepEqual(parseOpened('{nope'), {})
  assert.deepEqual(parseOpened('[1,2]'), {})
  assert.deepEqual(parseOpened('{"1": 5, "2": "x", "3": null}'), { '1': 5 })
})
