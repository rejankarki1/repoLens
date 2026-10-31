/**
 * Pure helpers for citations in a chat answer: turning `[S1]` markers into links, and what a citation points at.
 * The backend validates citations; nothing here invents one.
 */
import type { SourceCitation } from '@/lib/api/chat'

export const CITE_SCHEME = 'cite:'
// U+200B-U+200D, U+2060 and U+FEFF are zero-width characters models sometimes put inside a marker.
const GAP = '[\\s\\u200b-\\u200d\\u2060\\ufeff]*'
// Brackets may be ASCII [ ] or the full-width 【 】 / ［ ］ some models emit; the backend normalizes them on save.
const BRACKETED = new RegExp(`[\\[\\u3010\\uff3b]${GAP}(S\\d+(?:${GAP}[,;]${GAP}S\\d+)*)${GAP}[\\]\\u3011\\uff3d]`, 'g')
// "(S3, S6)": older saved answers (and some models) cite like this. Only linked when every label is a known source.
const PARENTHESISED = new RegExp(`\\(\\s*(S\\d+(?:${GAP}[,;]${GAP}S\\d+)*)\\s*\\)`, 'g')
const CODE_SPANS = /(```[\s\S]*?```|`[^`\n]*`)/

const labelsOf = (group: string) => group.match(/S\d+/g) ?? []
const link = (id: string) => `[${id}](${CITE_SCHEME}${id})`

/**
 * Turns `[S1]` / `[S1, S2]` (and a parenthesised group whose labels are all known) into markdown links with a
 * private `cite:` scheme. A bracketed marker for an unknown source is dropped, so the UI never shows a citation the
 * server did not validate; an unknown parenthesised group is ordinary text and is left alone. Code is untouched.
 */
export function linkCitations(text: string, known: ReadonlySet<string>): string {
  return text
    .split(CODE_SPANS)
    .map((part, i) => {
      if (i % 2 === 1) return part
      const bracketed = part.replace(BRACKETED, (_, group: string) => labelsOf(group).filter((id) => known.has(id)).map(link).join(' '))
      return bracketed.replace(PARENTHESISED, (whole, group: string) => {
        const labels = labelsOf(group)
        return labels.every((id) => known.has(id)) ? labels.map(link).join(' ') : whole
      })
    })
    .join('')
}

export function formatRange(start: number, end: number): string {
  return start === end ? `${start}` : `${start}-${end}`
}

/** The Code Explorer deep link for a cited range. */
export function citationHref(repoId: string, c: Pick<SourceCitation, 'path' | 'startLine' | 'endLine'>): string {
  return `/repos/${repoId}?tab=code&file=${encodeURIComponent(c.path)}&lines=${formatRange(c.startLine, c.endLine)}`
}

/** A citation is from another snapshot when its commit is not the repository's current one (`current` is a short sha). */
export function isStaleCitation(c: Pick<SourceCitation, 'commitSha'>, current: string | null): boolean {
  return !!current && !!c.commitSha && !c.commitSha.startsWith(current)
}

/**
 * Whether a click should open the in-page preview. Anything that means "open this link elsewhere" (a modifier key, a
 * middle click) is left to the browser, so the plain link still works as direct navigation.
 */
export function shouldPreview(event: { button: number; metaKey: boolean; ctrlKey: boolean; shiftKey: boolean; altKey: boolean }): boolean {
  return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey
}

/**
 * What to tell the reader about the cited range, given the file now stored: a warning when the file is shorter than
 * the cited lines (it changed), otherwise null.
 */
export function rangeProblem(c: Pick<SourceCitation, 'startLine' | 'endLine'>, totalLines: number): string | null {
  if (c.startLine > totalLines) return `The cited lines ${formatRange(c.startLine, c.endLine)} are past the end of this file (${totalLines} lines).`
  if (c.endLine > totalLines) return `The cited range ends at line ${c.endLine}, but the file has ${totalLines} lines.`
  return null
}
