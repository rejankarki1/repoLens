/** Wording for the visitor's free questions: pure, so the rules are unit-testable. */

import type { GuestAsk } from './guest-ask'

/** Mirrors the backend's default `rag_question_max_chars`. */
export const GUEST_MAX_QUESTION_CHARS = 2_000

function waitText(seconds: number): string {
  if (seconds < 90) return 'a minute'
  if (seconds < 5400) return `${Math.round(seconds / 60)} minutes`
  return `${Math.round(seconds / 3600)} hours`
}

export function describeGuestQuota(ask: GuestAsk): { text: string; canAsk: boolean } {
  if (ask.remaining > 0) {
    const noun = ask.remaining === 1 ? 'question' : 'questions'
    return { text: `${ask.remaining} free ${noun} left today`, canAsk: true }
  }
  const reset = ask.resetsInSeconds ? `; more in about ${waitText(ask.resetsInSeconds)}` : ''
  return { text: `No free questions left today${reset}`, canAsk: false }
}

/** User-facing text for a failed free question. A quota refusal already says which limit and when it resets. */
export function describeGuestError(err: { code: string; message: string }): string {
  switch (err.code) {
    case 'quota':
      return err.message
    case 'not_found':
      return 'Free questions are not available for this repository right now.'
    case 'not_ready':
      return err.message
    case 'rate_limited':
      return 'The AI provider is rate limiting requests. Wait a moment and try again.'
    case 'timeout':
      return 'The answer took too long. Try a narrower question.'
    default:
      return err.message || 'The question could not be answered. Try again in a moment.'
  }
}
