import type { Citation, CodeLanguage, SourceFile } from '@/lib/types'

/** Strip the leading newline and common indentation of a template literal block. */
export function code(source: string): string {
  const lines = source.replace(/^\n/, '').replace(/\s+$/, '').split('\n')
  const indent = Math.min(
    ...lines.filter((l) => l.trim().length > 0).map((l) => l.match(/^ */)?.[0].length ?? 0),
  )
  return lines.map((l) => l.slice(indent)).join('\n')
}

export function languageFromPath(path: string): CodeLanguage {
  const ext = path.split('.').pop()?.toLowerCase()
  switch (ext) {
    case 'ts':
      return 'typescript'
    case 'tsx':
      return 'tsx'
    case 'js':
    case 'mjs':
    case 'cjs':
    case 'jsx':
      return 'javascript'
    case 'py':
      return 'python'
    case 'json':
      return 'json'
    case 'md':
      return 'markdown'
    case 'toml':
      return 'toml'
    default:
      return 'text'
  }
}

type FileInput = Omit<SourceFile, 'language' | 'imports' | 'exports'> &
  Partial<Pick<SourceFile, 'imports' | 'exports'>>

export function file(input: FileInput): SourceFile {
  return {
    imports: [],
    exports: [],
    ...input,
    language: languageFromPath(input.path),
  }
}

/**
 * Build a citation by locating an anchor string inside a file, so line ranges
 * stay correct even when mock source content is edited.
 */
export function createCiter(files: SourceFile[]) {
  const byPath = new Map(files.map((f) => [f.path, f]))
  return function cite(path: string, anchor: string, span = 8): Citation {
    const target = byPath.get(path)
    if (!target) throw new Error(`[mock] Unknown citation path: ${path}`)
    const lines = target.content.split('\n')
    const index = lines.findIndex((l) => l.includes(anchor))
    const startLine = index === -1 ? 1 : index + 1
    const endLine = Math.min(lines.length, startLine + span - 1)
    return { path, startLine, endLine }
  }
}
