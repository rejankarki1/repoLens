export type IndexingState = 'pending' | 'queued' | 'importing' | 'indexing' | 'completed' | 'failed'

export interface LanguageShare {
  name: string
  percent: number
}

export interface RepositoryStats {
  files: number
  directories: number
  linesOfCode: number
}

export interface Repository {
  id: string
  owner: string
  name: string
  fullName: string
  url: string
  description: string
  primaryLanguage: string
  languages: LanguageShare[]
  defaultBranch: string
  indexedCommit: string | null
  license: string | null
  lastAnalyzedAt: string | null
  status: IndexingState
  /** 0-100 while importing or indexing; null when the backend reports no percentage */
  progress: number | null
  error: string | null
  stats: RepositoryStats | null
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

export type ModuleGroup = 'entry' | 'routing' | 'middleware' | 'service' | 'data' | 'core' | 'util' | 'ui'

export interface SourceFile {
  path: string
  language: CodeLanguage
  /** Human-readable language name from the backend, when it differs from the highlighter's */
  languageLabel?: string
  /** Line count reported by the backend */
  lineCount?: number
  content: string
  /** Paths of other files in the repository that this file imports */
  imports: string[]
  /** Exported symbol names */
  exports: string[]
  /** Short summary of the file's responsibility (sample AI-generated) */
  summary?: string
  /** Files with a group participate in the architecture graph */
  group?: ModuleGroup
}

export interface FileTreeNode {
  name: string
  path: string
  type: 'file' | 'directory'
  children?: FileTreeNode[]
}

export interface Citation {
  path: string
  startLine: number
  endLine: number
}

export interface ModuleHighlight {
  path: string
  name: string
  description: string
}

export interface RepositoryInsights {
  /** Sample AI-generated architecture summary */
  summary: string
  entryPoints: ModuleHighlight[]
  keyModules: ModuleHighlight[]
}

export interface MockAnswer {
  keywords: string[]
  content: string
  citations: Citation[]
}

export interface RepositoryDataset {
  files: SourceFile[]
  insights: RepositoryInsights
  answers: MockAnswer[]
  fallbackAnswer: Omit<MockAnswer, 'keywords'>
  isPlaceholder?: boolean
}

export type ChatRole = 'user' | 'assistant'

export interface ChatMessage {
  id: string
  role: ChatRole
  content: string
  citations: Citation[]
  createdAt: string
  status: 'complete' | 'thinking' | 'streaming'
}

export interface Conversation {
  id: string
  title: string
  messages: ChatMessage[]
  updatedAt: string
}

export interface GraphNodeData {
  path: string
  label: string
  group: ModuleGroup
  imports: string[]
  importedBy: string[]
  exports: string[]
  summary?: string
}

export interface DependencyGraph {
  nodes: GraphNodeData[]
  edges: { source: string; target: string }[]
}
