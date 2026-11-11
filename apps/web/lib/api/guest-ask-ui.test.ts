import assert from 'node:assert/strict'
import { test } from 'node:test'
import { describeGuestError, describeGuestQuota } from './guest-ask-ui.ts'

const ask = (over: Partial<{ remaining: number; resetsInSeconds: number | null }>) => ({
  enabled: true, limit: 3, remaining: 3, resetsInSeconds: null, ...over,
})

test('questions left are shown with the right noun and the composer stays open', () => {
  assert.deepEqual(describeGuestQuota(ask({ remaining: 3 })), { text: '3 free questions left today', canAsk: true })
  assert.deepEqual(describeGuestQuota(ask({ remaining: 1 })), { text: '1 free question left today', canAsk: true })
})

test('with none left the composer closes and says when more come back', () => {
  assert.deepEqual(describeGuestQuota(ask({ remaining: 0, resetsInSeconds: 7200 })), {
    text: 'No free questions left today; more in about 2 hours', canAsk: false,
  })
  assert.equal(describeGuestQuota(ask({ remaining: 0, resetsInSeconds: 30 })).text, 'No free questions left today; more in about a minute')
  assert.equal(describeGuestQuota(ask({ remaining: 0 })).text, 'No free questions left today')
})

test('a refused quota shows the server message; other failures get plain wording', () => {
  assert.equal(describeGuestError({ code: 'quota', message: 'You have used your 3 free questions for today. Try again in about 5 hours.' }), 'You have used your 3 free questions for today. Try again in about 5 hours.')
  assert.match(describeGuestError({ code: 'not_found', message: 'x' }), /not available/)
  assert.equal(describeGuestError({ code: 'unavailable', message: '' }), 'The question could not be answered. Try again in a moment.')
  assert.equal(describeGuestError({ code: 'unavailable', message: 'Answer generation is temporarily unavailable.' }), 'Answer generation is temporarily unavailable.')
})
