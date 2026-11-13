import assert from 'node:assert/strict'
import { test } from 'node:test'
import { highlightLines, MAX_HIGHLIGHT_CHARS, MAX_HIGHLIGHT_LINES, shouldHighlight } from './highlight.ts'

test('ordinary files are highlighted', () => {
  assert.equal(shouldHighlight('const a = 1\nconst b = 2\n'), true)
  assert.equal(shouldHighlight('x\n'.repeat(MAX_HIGHLIGHT_LINES - 1)), true)
})

test('a file over the line limit is not highlighted, even when it is small in bytes', () => {
  const source = 'x\n'.repeat(MAX_HIGHLIGHT_LINES + 1)
  assert.ok(source.length < MAX_HIGHLIGHT_CHARS)
  assert.equal(shouldHighlight(source), false)
})

test('a file over the character limit is not highlighted, even when it is one line (a minified bundle)', () => {
  assert.equal(shouldHighlight('a'.repeat(MAX_HIGHLIGHT_CHARS + 1)), false)
})

test('a known line count is trusted instead of rescanning the text', () => {
  assert.equal(shouldHighlight('short', MAX_HIGHLIGHT_LINES + 1), false)
  assert.equal(shouldHighlight('short', 10), true)
})

test('the plain-text path keeps one row per line, so line numbers and ranges still line up', () => {
  const source = 'def f():\n    return 1\n\nprint(f())'
  const plain = highlightLines(source, 'text')
  assert.equal(plain.length, 4)
  assert.deepEqual(plain[0], [{ type: 'plain', value: 'def f():' }])
  assert.deepEqual(plain[2], [])
  // Same row count as the highlighted rendering of the same file.
  assert.equal(highlightLines(source, 'python').length, 4)
})
