import { mutate } from 'swr'
import { buildFileTree } from '@/lib/repo-utils'
import type { FileSummary } from '@/lib/repo-stats'
import type { CodeLanguage, FileTreeNode, IndexingState, Repository, SourceFile } from '@/lib/types'

/** Inlined at build time (see `.env.example`). */
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? 'http://127.0.0.1:8765').replace(/\/+$/, '')

/* ------------------------------ Backend shapes ------------------------------ */

interface ApiRepository {
  id: number
  github_repo_id: number
  full_name: string
  default_branch: string
  created_at: string
  ingestion_status: 'pending' | 'queued' | 'in_progress' | 'completed' | 'failed'
  last_ingested_commit_sha: string | null
  ingestion_truncated: boolean
  last_ingested_at: string | null
  ingestion_error: string | null
}

interface ApiFileSummary {
  path: string
  language: string
  category: string
  size_bytes: number
  line_count: number
}

interface ApiFileContent extends ApiFileSummary {
  content: string
}

/* --------------------------------- Fetching --------------------------------- */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message)
  }
}

function describeDetail(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail
  // FastAPI validation errors are a list of { msg } objects.
  if (Array.isArray(detail) && detail.length > 0 && typeof detail[0]?.msg === 'string') return detail[0].msg
  return fallback
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_URL}${path}`, init)
  } catch {
    throw new ApiError(0, `Could not reach the RepoLens API at ${API_URL}. Is the backend running?`)
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new ApiError(response.status, describeDetail(body?.detail, `Request failed (${response.status})`))
  }
  return response.json() as Promise<T>
}

/* --------------------------------- Adapters --------------------------------- */

const STATUS_MAP: Record<ApiRepository['ingestion_status'], IndexingState> = {
  pending: 'pending',
  queued: 'queued',
  in_progress: 'indexing',
  completed: 'completed',
  failed: 'failed',
}

/** Maps a backend repository onto the UI's `Repository`. Unavailable fields get neutral values. */
function toRepository(api: ApiRepository): Repository {
  const [owner, ...rest] = api.full_name.split('/')
  return {
    id: String(api.id),
    owner,
    name: rest.join('/'),
    fullName: api.full_name,
    url: `https://github.com/${api.full_name}`,
    description: '',
    primaryLanguage: 'Unknown',
    defaultBranch: api.default_branch,
    indexedCommit: api.last_ingested_commit_sha ? api.last_ingested_commit_sha.slice(0, 7) : null,
    lastAnalyzedAt: api.last_ingested_at,
    status: STATUS_MAP[api.ingestion_status] ?? 'pending',
    // The backend reports no percentage; null renders an indeterminate bar.
    progress: null,
    error: api.ingestion_status === 'failed' ? api.ingestion_error : null,
  }
}

/* -------------------------------- Repositories ------------------------------ */

export async function listRepositories(): Promise<Repository[]> {
  const rows = await request<ApiRepository[]>('/repositories')
  // The API lists by ascending id; show the newest import first.
  return rows.map(toRepository).reverse()
}

export async function getRepository(id: string): Promise<Repository> {
  return toRepository(await request<ApiRepository>(`/repositories/${encodeURIComponent(id)}`))
}

export async function importRepository(githubUrl: string): Promise<Repository> {
  return toRepository(
    await request<ApiRepository>('/repositories', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ github_url: githubUrl }),
    }),
  )
}

/* ---------------------------------- Files ----------------------------------- */

/** Backend language names the syntax highlighter understands; everything else renders as plain text. */
const HIGHLIGHT_LANGUAGES: Record<string, CodeLanguage> = {
  python: 'python',
  javascript: 'javascript',
  typescript: 'typescript',
  json: 'json',
  markdown: 'markdown',
}

function toCodeLanguage(language: string): CodeLanguage {
  return HIGHLIGHT_LANGUAGES[language] ?? 'text'
}

function toLanguageLabel(language: string): string {
  if (!language || language === 'unknown') return 'Plain text'
  return language.charAt(0).toUpperCase() + language.slice(1)
}

/** The flat, metadata-only file list of the stored snapshot (no source text). */
export async function getFileSummaries(id: string): Promise<FileSummary[]> {
  const files = await request<ApiFileSummary[]>(`/repositories/${encodeURIComponent(id)}/files`)
  return files.map((f) => ({ path: f.path, language: f.language, category: f.category, sizeBytes: f.size_bytes, lineCount: f.line_count }))
}

/** Folder tree built from the file list. */
export function toFileTree(files: FileSummary[]): { tree: FileTreeNode[] } {
  return { tree: buildFileTree(files) }
}

/** One file's stored source text. Only called when the user selects a file. */
export async function getFile(id: string, path: string): Promise<SourceFile> {
  const file = await request<ApiFileContent>(
    `/repositories/${encodeURIComponent(id)}/files/content?path=${encodeURIComponent(path)}`,
  )
  return {
    path: file.path,
    language: toCodeLanguage(file.language),
    languageLabel: toLanguageLabel(file.language),
    lineCount: file.line_count,
    content: file.content,
  }
}

/* -------------------------------- Ingestion --------------------------------- */

/**
 * Asks the backend to queue an ingestion (`202 Accepted`). The work happens in an RQ worker, so this
 * returns as soon as the job is queued; progress is read by polling the repository (see `hooks.ts`).
 *
 * PostgreSQL is the authority on duplicates: if an ingestion is already queued or running the
 * backend answers 409, which callers can treat as "already in progress". Whatever the outcome, the
 * cached repository is refreshed so the UI shows the status the server actually has.
 */
export async function startIngestion(id: string): Promise<void> {
  try {
    await request<unknown>(`/repositories/${encodeURIComponent(id)}/ingestions`, { method: 'POST' })
  } finally {
    void mutate(['repository', id])
    void mutate('repositories')
  }
}

/** Turns an ingestion-request failure into a message for the user. */
export function describeIngestionError(err: unknown): { message: string; alreadyActive: boolean } {
  if (err instanceof ApiError && err.status === 409) {
    return { message: 'An ingestion for this repository is already queued or running.', alreadyActive: true }
  }
  return { message: err instanceof Error ? err.message : 'Could not start ingestion.', alreadyActive: false }
}
