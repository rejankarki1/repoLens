import type {
  ChatMessage,
  Conversation,
  FileTreeNode,
  MockAnswer,
  Repository,
  SourceFile,
} from '@/lib/types'
import { seedRepositories } from '@/lib/mock/repositories'
import { getDataset } from '@/lib/mock/datasets'
import { buildFileTree, parseGitHubUrl } from '@/lib/repo-utils'

/**
 * In-browser mock of the RepoLens backend. Every function mirrors an endpoint
 * the FastAPI service is expected to expose, so swapping this module for real
 * fetch() calls should not require changes to UI components.
 *
 * State lives in memory and resets on reload.
 */

const IMPORT_MS = 2_500
const INDEX_MS = 6_000

interface Job {
  startedAt: number
  willFail: boolean
}

let repositories: Repository[] = structuredClone(seedRepositories)
const jobs = new Map<string, Job>()
const conversations = new Map<string, Conversation[]>()

// The seeded "indexing" repository advances as soon as the app loads.
jobs.set('tiangolo-fastapi', { startedAt: Date.now() - IMPORT_MS - INDEX_MS * 0.42, willFail: false })

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms))
const uid = () => Math.random().toString(36).slice(2, 10)

function advanceJobs() {
  const now = Date.now()
  repositories = repositories.map((repo) => {
    const job = jobs.get(repo.id)
    if (!job) return repo
    const elapsed = now - job.startedAt

    if (elapsed < IMPORT_MS) {
      return { ...repo, status: 'importing', progress: Math.round((elapsed / IMPORT_MS) * 100) }
    }
    if (job.willFail && elapsed > IMPORT_MS + INDEX_MS * 0.55) {
      jobs.delete(repo.id)
      return {
        ...repo,
        status: 'failed',
        progress: 0,
        lastAnalyzedAt: new Date().toISOString(),
        error: 'Indexing timed out while parsing the source tree. Try re-analyzing the repository.',
      }
    }
    if (elapsed < IMPORT_MS + INDEX_MS) {
      return { ...repo, status: 'indexing', progress: Math.round(((elapsed - IMPORT_MS) / INDEX_MS) * 100) }
    }

    jobs.delete(repo.id)
    const files = getDataset(repo).files
    const dirs = new Set(files.flatMap((f) => f.path.split('/').slice(0, -1).map((_, i, a) => a.slice(0, i + 1).join('/'))))
    return {
      ...repo,
      status: 'completed',
      progress: 100,
      error: null,
      lastAnalyzedAt: new Date().toISOString(),
      indexedCommit: repo.indexedCommit ?? uid().slice(0, 7),
      stats: repo.stats ?? {
        files: files.length,
        directories: dirs.size,
        linesOfCode: files.reduce((n, f) => n + f.content.split('\n').length, 0),
      },
    }
  })
}

function findRepository(id: string): Repository {
  advanceJobs()
  const repo = repositories.find((r) => r.id === id)
  if (!repo) throw new Error('Repository not found')
  return repo
}

export async function listRepositories(): Promise<Repository[]> {
  await delay(250)
  advanceJobs()
  return repositories
}

export async function getRepository(id: string): Promise<Repository> {
  await delay(150)
  return findRepository(id)
}

export async function importRepository(url: string): Promise<Repository> {
  await delay(400)
  const parsed = parseGitHubUrl(url)
  if (!parsed.ok) throw new Error(parsed.error)

  const id = `${parsed.owner}-${parsed.name}`.toLowerCase().replace(/[^a-z0-9-]/g, '-')
  const existing = repositories.find((r) => r.id === id)
  if (existing) {
    if (existing.status === 'importing' || existing.status === 'indexing') return existing
    return reanalyzeRepository(id)
  }

  const repo: Repository = {
    id,
    owner: parsed.owner,
    name: parsed.name,
    fullName: `${parsed.owner}/${parsed.name}`,
    url: `https://github.com/${parsed.owner}/${parsed.name}`,
    description: 'Imported in demo mode. Description will be fetched from GitHub once the backend is connected.',
    primaryLanguage: 'Unknown',
    languages: [],
    defaultBranch: 'main',
    indexedCommit: null,
    license: null,
    lastAnalyzedAt: null,
    status: 'importing',
    progress: 0,
    error: null,
    stats: null,
  }
  repositories = [repo, ...repositories]
  // Repository names containing "fail" simulate an indexing failure.
  jobs.set(id, { startedAt: Date.now(), willFail: /fail/i.test(parsed.name) })
  return repo
}

export async function reanalyzeRepository(id: string): Promise<Repository> {
  await delay(200)
  const repo = findRepository(id)
  jobs.set(id, { startedAt: Date.now(), willFail: false })
  repositories = repositories.map((r) => (r.id === id ? { ...r, status: 'importing', progress: 0, error: null } : r))
  return { ...repo, status: 'importing', progress: 0, error: null }
}

export async function deleteRepository(id: string): Promise<void> {
  await delay(150)
  repositories = repositories.filter((r) => r.id !== id)
  jobs.delete(id)
}

export async function getFileTree(repoId: string): Promise<{ tree: FileTreeNode[]; files: Omit<SourceFile, 'content'>[] }> {
  await delay(200)
  const { files } = getDataset(findRepository(repoId))
  return {
    tree: buildFileTree(files),
    files: files.map(({ content: _content, ...meta }) => meta),
  }
}

export async function getFile(repoId: string, path: string): Promise<SourceFile> {
  await delay(120)
  const found = getDataset(findRepository(repoId)).files.find((f) => f.path === path)
  if (!found) throw new Error(`File not found: ${path}`)
  return found
}

export async function getRepositoryInsights(repoId: string) {
  await delay(300)
  const dataset = getDataset(findRepository(repoId))
  return { ...dataset.insights, isPlaceholder: Boolean(dataset.isPlaceholder) }
}

/* ---------------------------------- Chat --------------------------------- */

export async function listConversations(repoId: string): Promise<Conversation[]> {
  await delay(100)
  return [...(conversations.get(repoId) ?? [])].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
}

export function createConversation(repoId: string, firstQuestion: string): Conversation {
  const conversation: Conversation = {
    id: uid(),
    title: firstQuestion.length > 48 ? `${firstQuestion.slice(0, 48)}\u2026` : firstQuestion,
    messages: [],
    updatedAt: new Date().toISOString(),
  }
  conversations.set(repoId, [conversation, ...(conversations.get(repoId) ?? [])])
  return conversation
}

export function deleteConversation(repoId: string, conversationId: string) {
  conversations.set(
    repoId,
    (conversations.get(repoId) ?? []).filter((c) => c.id !== conversationId),
  )
}

function saveConversation(repoId: string, conversation: Conversation) {
  conversations.set(
    repoId,
    (conversations.get(repoId) ?? []).map((c) => (c.id === conversation.id ? conversation : c)),
  )
}

function pickAnswer(repo: Repository, question: string): Omit<MockAnswer, 'keywords'> {
  const dataset = getDataset(repo)
  const q = question.toLowerCase()
  let best: MockAnswer | null = null
  let bestScore = 0
  for (const answer of dataset.answers) {
    const score = answer.keywords.reduce((n, k) => n + (q.includes(k) ? 1 : 0), 0)
    if (score > bestScore) {
      best = answer
      bestScore = score
    }
  }
  return best ?? dataset.fallbackAnswer
}

export interface AskStreamHandlers {
  onUpdate: (conversation: Conversation) => void
  signal?: AbortSignal
}

/**
 * Simulates a streaming answer. The real backend would stream tokens over SSE;
 * here the canned answer is revealed in small chunks.
 */
export async function askQuestion(
  repoId: string,
  conversationId: string,
  question: string,
  { onUpdate, signal }: AskStreamHandlers,
): Promise<Conversation> {
  const repo = findRepository(repoId)
  const existing = (conversations.get(repoId) ?? []).find((c) => c.id === conversationId)
  if (!existing) throw new Error('Conversation not found')

  const now = new Date().toISOString()
  const userMessage: ChatMessage = { id: uid(), role: 'user', content: question, citations: [], createdAt: now, status: 'complete' }
  const assistant: ChatMessage = { id: uid(), role: 'assistant', content: '', citations: [], createdAt: now, status: 'thinking' }

  let conversation: Conversation = { ...existing, messages: [...existing.messages, userMessage, assistant], updatedAt: now }
  const emit = () => {
    saveConversation(repoId, conversation)
    onUpdate(conversation)
  }
  const patchAssistant = (patch: Partial<ChatMessage>) => {
    conversation = {
      ...conversation,
      messages: conversation.messages.map((m) => (m.id === assistant.id ? { ...m, ...patch } : m)),
    }
    emit()
  }
  emit()

  await delay(900)
  const answer = pickAnswer(repo, question)
  const tokens = answer.content.match(/\S+\s*/g) ?? []
  let revealed = ''

  for (let i = 0; i < tokens.length; i += 3) {
    if (signal?.aborted) break
    revealed += tokens.slice(i, i + 3).join('')
    patchAssistant({ content: revealed, status: 'streaming' })
    await delay(28)
  }

  patchAssistant({
    content: signal?.aborted ? revealed : answer.content,
    citations: answer.citations,
    status: 'complete',
  })
  return conversation
}
