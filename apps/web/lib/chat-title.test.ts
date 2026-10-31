import assert from 'node:assert/strict'
import { test } from 'node:test'
import { makeTitle } from './chat-title.ts'

// The same cases as tests/test_rag_api.py::test_titles_are_deterministic_and_short.
const CASES: [string, string | null][] = [
  ['What does this project do and how is it structured?', 'What does this project do and how is it structured'],
  ['  hey,   please   explain the  auth flow.  Thanks a lot!', 'Explain the auth flow'],
  ['is this app deployed ?', 'Is this app deployed'],
  ['Where is the entry point? And how are routes registered?', 'Where is the entry point'],
  ['In URLSafeSerializerMixin.dump_payload, what exact condition determines whether compression is used?', 'In URLSafeSerializerMixin.dump_payload, what exact…'],
  ['x'.repeat(100), 'X' + 'x'.repeat(59) + '…'],
  ['?', null],
  ['   ', null],
  ['Hello', 'Hello'],
]

for (const [question, title] of CASES) {
  test(`title for ${JSON.stringify(question.slice(0, 40))}`, () => {
    assert.equal(makeTitle(question), title)
    assert.equal(makeTitle(question), makeTitle(question))
  })
}
