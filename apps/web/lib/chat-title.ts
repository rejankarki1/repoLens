/**
 * A short conversation title from the first question, with no model call. This mirrors `make_title` in
 * apps/api/services/rag.py rule for rule (the same test cases run on both sides), so a title looks the same in the
 * moment before it is saved as after.
 */
export const TITLE_MAX_CHARS = 60

const GREETING = /^(?:(?:hi|hello|hey)\b[,!.]?\s+)?(?:please\b[,]?\s+)?/i
const FIRST_SENTENCE = /^(.{12,}?[.?!])(?:\s|$)/

export function makeTitle(question: string): string | null {
  let text = question.replace(/\s+/g, ' ').trim()
  text = text.replace(GREETING, '').trim()
  const sentence = FIRST_SENTENCE.exec(text)
  if (sentence) text = sentence[1]
  text = text.replace(/[ .?!,;:]+$/, '')
  if (!text) return null
  text = text[0].toUpperCase() + text.slice(1)
  if (text.length > TITLE_MAX_CHARS) {
    const cut = text.slice(0, TITLE_MAX_CHARS)
    const space = cut.lastIndexOf(' ')
    text = (space >= TITLE_MAX_CHARS - 20 ? cut.slice(0, space) : cut).replace(/[ ,;:-]+$/, '') + '…'
  }
  return text
}
