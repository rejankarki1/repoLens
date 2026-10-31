import assert from 'node:assert/strict'
import { test } from 'node:test'
import { summarizeFiles, type FileSummary } from './repo-stats.ts'

const file = (path: string, language: string, lineCount: number): FileSummary => ({ path, language, category: 'source', sizeBytes: lineCount * 20, lineCount })

test('counts files, directories (every ancestor once) and lines', () => {
  const stats = summarizeFiles([file('a.py', 'python', 10), file('src/x/b.ts', 'typescript', 30), file('src/x/c.ts', 'typescript', 20), file('src/d.md', 'markdown', 5)])
  assert.deepEqual([stats.files, stats.directories, stats.lines], [4, 2, 65])
})

test('languages are ranked by lines, unknown files are not counted, and percentages add up to 100', () => {
  const stats = summarizeFiles([file('a.ts', 'typescript', 50), file('b.py', 'python', 30), file('c.md', 'markdown', 20), file('LICENSE', 'unknown', 500), file('e.json', 'json', 0)])
  assert.deepEqual(stats.languages.map((l) => [l.name, l.percent]), [['TypeScript', 50], ['Python', 30], ['Markdown', 20]])
  assert.equal(stats.lines, 600)
})

test('the long tail folds into Other and rounding never breaks the 100 total', () => {
  const langs = ['typescript', 'python', 'sql', 'yaml', 'toml', 'shell', 'css']
  const stats = summarizeFiles(langs.map((l, i) => file(`f${i}`, l, 10)), 3)
  assert.equal(stats.languages.length, 4)
  assert.equal(stats.languages.at(-1)!.name, 'Other')
  assert.equal(stats.languages.reduce((s, l) => s + l.percent, 0), 100)
})

test('an empty repository has no languages and no NaN', () => {
  assert.deepEqual(summarizeFiles([]), { files: 0, directories: 0, lines: 0, languages: [] })
})
