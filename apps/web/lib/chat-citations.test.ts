import assert from 'node:assert/strict'
import { test } from 'node:test'
import { citationHref, isStaleCitation, linkCitations, rangeProblem, shouldPreview } from './chat-citations.ts'

const known = new Set(['S1', 'S2'])

test('bracketed markers of known sources become links, unknown ones are dropped', () => {
  assert.equal(linkCitations('Hashes [S1] and [S2, S9] but not [S7].', known), 'Hashes [S1](cite:S1) and [S2](cite:S2) but not .')
})

test('full-width brackets and zero-width characters are recognised', () => {
  assert.equal(linkCitations('a 【S1】 b [\u200bS2]', known), 'a [S1](cite:S1) b [S2](cite:S2)')
})

test('a parenthesised group is linked only when every label is a known source', () => {
  assert.equal(linkCitations('See (S1, S2) here.', known), 'See [S1](cite:S1) [S2](cite:S2) here.')
  assert.equal(linkCitations('See (S1, S9) and (S7) and (see S1).', known), 'See (S1, S9) and (S7) and (see S1).')
})

test('code is never rewritten', () => {
  const text = 'Use `x[S1]` and\n```\nlist[S1]\n```\nbut [S1] here'
  assert.equal(linkCitations(text, known), 'Use `x[S1]` and\n```\nlist[S1]\n```\nbut [S1](cite:S1) here')
})

test('citation links point at the real cited range in the Code Explorer', () => {
  assert.equal(citationHref('3', { path: 'src/a b.ts', startLine: 4, endLine: 9 }), '/repos/3?tab=code&file=src%2Fa%20b.ts&lines=4-9')
  assert.equal(citationHref('3', { path: 'a.py', startLine: 7, endLine: 7 }), '/repos/3?tab=code&file=a.py&lines=7')
})

test('a citation from another commit is stale; one from the current commit is not', () => {
  assert.equal(isStaleCitation({ commitSha: 'a'.repeat(40) }, 'aaaaaaa'), false)
  assert.equal(isStaleCitation({ commitSha: 'b'.repeat(40) }, 'aaaaaaa'), true)
  assert.equal(isStaleCitation({ commitSha: 'b'.repeat(40) }, null), false)
})

test('plain clicks open the preview; modified clicks keep the browser behaviour', () => {
  const click = { button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false }
  assert.equal(shouldPreview(click), true)
  assert.equal(shouldPreview({ ...click, metaKey: true }), false)
  assert.equal(shouldPreview({ ...click, ctrlKey: true }), false)
  assert.equal(shouldPreview({ ...click, shiftKey: true }), false)
  assert.equal(shouldPreview({ ...click, button: 1 }), false)
})

test('a cited range outside the stored file is reported, never silently shown', () => {
  assert.equal(rangeProblem({ startLine: 5, endLine: 9 }, 100), null)
  assert.match(rangeProblem({ startLine: 120, endLine: 130 }, 100) ?? '', /past the end/)
  assert.match(rangeProblem({ startLine: 90, endLine: 130 }, 100) ?? '', /ends at line 130/)
})
