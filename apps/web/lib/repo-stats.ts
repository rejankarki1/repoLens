/** Numbers shown on the repository overview, computed from the stored file list (never from a model). */

export interface FileSummary {
  path: string
  /** Backend language name, lowercase ("typescript", "markdown", "unknown"...). */
  language: string
  category: string
  sizeBytes: number
  lineCount: number
}

export interface LanguageShare {
  name: string
  lines: number
  /** Whole percent of the counted lines; the shares of one result add up to 100. */
  percent: number
}

export interface RepositoryStats {
  files: number
  directories: number
  lines: number
  languages: LanguageShare[]
}

const LABELS: Record<string, string> = {
  typescript: 'TypeScript', javascript: 'JavaScript', python: 'Python', json: 'JSON', markdown: 'Markdown', sql: 'SQL',
  yaml: 'YAML', toml: 'TOML', rst: 'reStructuredText', makefile: 'Makefile', shell: 'Shell', html: 'HTML', css: 'CSS',
}

export function languageLabel(language: string): string {
  return LABELS[language] ?? language.charAt(0).toUpperCase() + language.slice(1)
}

/** Languages are ranked by lines; files of unknown type are not counted, and the long tail is folded into "Other". */
export function summarizeFiles(files: FileSummary[], top = 5): RepositoryStats {
  const dirs = new Set<string>()
  const byLanguage = new Map<string, number>()
  let lines = 0
  for (const f of files) {
    const parts = f.path.split('/')
    for (let i = 1; i < parts.length; i++) dirs.add(parts.slice(0, i).join('/'))
    lines += f.lineCount
    if (f.language !== 'unknown' && f.lineCount > 0) byLanguage.set(f.language, (byLanguage.get(f.language) ?? 0) + f.lineCount)
  }
  const ranked = [...byLanguage.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))
  const shown = ranked.slice(0, top).map(([name, n]) => ({ name: languageLabel(name), lines: n }))
  const rest = ranked.slice(top).reduce((sum, [, n]) => sum + n, 0)
  if (rest > 0) shown.push({ name: 'Other', lines: rest })
  const total = shown.reduce((sum, l) => sum + l.lines, 0)

  // largest-remainder rounding, so the percentages add up to exactly 100
  const exact = shown.map((l) => (total ? (l.lines / total) * 100 : 0))
  const floors = exact.map(Math.floor)
  let missing = total ? 100 - floors.reduce((a, b) => a + b, 0) : 0
  const order = exact.map((x, i) => [x - Math.floor(x), i] as const).sort((a, b) => b[0] - a[0] || a[1] - b[1])
  for (const [, i] of order) {
    if (missing-- <= 0) break
    floors[i] += 1
  }
  return { files: files.length, directories: dirs.size, lines, languages: shown.map((l, i) => ({ ...l, percent: floors[i] })) }
}
