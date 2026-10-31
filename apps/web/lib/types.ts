export type IndexingState = 'pending' | 'queued' | 'importing' | 'indexing' | 'completed' | 'failed'

export interface Repository {
  id: string
  owner: string
  name: string
  fullName: string
  url: string
  description: string
  primaryLanguage: string
  defaultBranch: string
  indexedCommit: string | null
  lastAnalyzedAt: string | null
  status: IndexingState
  /** 0-100 while importing or indexing; null when the backend reports no percentage */
  progress: number | null
  error: string | null
}

export type CodeLanguage =
  | 'typescript'
  | 'tsx'
  | 'javascript'
  | 'python'
  | 'json'
  | 'markdown'
  | 'toml'
  | 'text'

export interface SourceFile {
  path: string
  language: CodeLanguage
  /** Human-readable language name from the backend, when it differs from the highlighter's */
  languageLabel?: string
  /** Line count reported by the backend */
  lineCount?: number
  content: string
}

export interface FileTreeNode {
  name: string
  path: string
  type: 'file' | 'directory'
  children?: FileTreeNode[]
}
