import { API_URL, ApiError } from '@/lib/api/client'
import { readSse } from '@/lib/api/sse'

/* ------------------------------ Backend shapes ------------------------------ */

interface ApiCitation {
  id: string
  chunk_id: number
  source_file_id: number
  path: string
  language: string | null
  kind: string
  symbol_name: string | null
  start_line: number
  end_line: number
  start_byte: number
  end_byte: number
  commit_sha: string
}

interface ApiMessage {
  id: number
  conversation_id: number
  role: 'user' | 'assistant' | 'system'
  content: string
  citations: ApiCitation[]
  created_at: string | null
}

interface ApiConversation {
  id: number
  repository_id: number | null
  title: string | null
  created_at: string
  updated_at?: string | null
  message_count?: number
}

interface ApiAnswer {
  user_message: ApiMessage
  assistant_message: ApiMessage
  citations: ApiCitation[]
  answer_status: 'answered' | 'no_evidence'
  warnings: string[]
  model: string | null
  retrieval: { commit_sha: string | null; mode: string; sources_used: number }
}

interface ApiRetrieval {
  commit_sha: string | null
  mode: string
  warnings: string[]
  sources: ApiCitation[]
}

export interface ApiSearchIndex {
  chunks: number
  embedded_chunks: number
  semantic: 'disabled' | 'not_built' | 'partial' | 'ready'
}

/* ---------------------------------- UI types ---------------------------------- */

/** A source the backend retrieved and validated; the only thing citations are rendered from. */
export interface SourceCitation {
  /** The `[S#]` marker used in the answer text */
  id: string
  path: string
  startLine: number
  endLine: number
  startByte: number
  endByte: number
  symbolName: string | null
  kind: string
  commitSha: string
}

export interface ConversationSummary {
  id: number
  /** Never empty: the saved title, or "Untitled chat" for a conversation that has none yet. */
  title: string
  createdAt: string
  /** Time of the latest message (the list endpoint only). */
  updatedAt: string | null
  messageCount: number
}

export interface RetrievalInfo {
  commitSha: string | null
  mode: string
  warnings: string[]
  sources: SourceCitation[]
}

export type TurnStatus = 'complete' | 'searching' | 'streaming' | 'failed' | 'stopped'

export interface ChatTurnMessage {
  /** Server id once saved, otherwise a client-only id */
  id: string
  role: 'user' | 'assistant'
  content: string
  citations: SourceCitation[]
  status: TurnStatus
  /** Set for failed answers */
  error?: { message: string; code: string; retryAfter: number | null }
  retrieval?: RetrievalInfo
  /** From the final answer: `no_evidence` means nothing relevant was found and the model was not called */
  noEvidence?: boolean
  warnings?: string[]
}

export interface StreamHandlers {
  onRetrieval: (info: RetrievalInfo) => void
  onToken: (text: string) => void
  onDone: (result: { user: ChatTurnMessage; assistant: ChatTurnMessage }) => void
}

/** A failed or interrupted question. `code` is the backend's error code or `network` / `interrupted`. */
export class ChatError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly retryAfter: number | null = null,
  ) {
    super(message)
  }
}

/* --------------------------------- Adapters ---------------------------------- */

export function toCitation(c: ApiCitation): SourceCitation {
  return {
    id: c.id,
    path: c.path,
    startLine: c.start_line,
    endLine: c.end_line,
    startByte: c.start_byte,
    endByte: c.end_byte,
    symbolName: c.symbol_name,
    kind: c.kind,
    commitSha: c.commit_sha,
  }
}

function toMessage(m: ApiMessage): ChatTurnMessage {
  return {
    id: String(m.id),
    role: m.role === 'assistant' ? 'assistant' : 'user',
    content: m.content,
    citations: (m.citations ?? []).map(toCitation),
    status: 'complete',
  }
}

const toConversation = (c: ApiConversation): ConversationSummary => ({
  id: c.id,
  title: c.title?.trim() || 'Untitled chat',
  createdAt: c.created_at,
  updatedAt: c.updated_at ?? null,
  messageCount: c.message_count ?? 0,
})

const base = (repoId: string) => `${API_URL}/repositories/${encodeURIComponent(repoId)}/conversations`

async function detailOf(response: Response): Promise<string> {
  const body = await response.json().catch(() => null)
  const detail = body?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && typeof detail[0]?.msg === 'string') return detail[0].msg
  return `Request failed (${response.status})`
}

/** A request that must succeed; failures become an `ApiError` with the server's message. */
async function send(url: string, init?: RequestInit): Promise<Response> {
  let response: Response
  try {
    response = await fetch(url, init)
  } catch {
    throw new ApiError(0, `Could not reach the RepoLens API at ${API_URL}. Is the backend running?`)
  }
  if (!response.ok) throw new ApiError(response.status, await detailOf(response))
  return response
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  return (await send(url, init)).json() as Promise<T>
}

/* ------------------------------- Conversations ------------------------------- */

export async function listConversations(repoId: string): Promise<ConversationSummary[]> {
  return (await json<ApiConversation[]>(base(repoId))).map(toConversation)
}

export async function getConversation(repoId: string, id: number): Promise<{ summary: ConversationSummary; messages: ChatTurnMessage[] }> {
  const data = await json<ApiConversation & { messages: ApiMessage[] }>(`${base(repoId)}/${id}`)
  return { summary: toConversation(data), messages: data.messages.filter((m) => m.role !== 'system').map(toMessage) }
}

export async function createConversation(repoId: string): Promise<ConversationSummary> {
  return toConversation(await json<ApiConversation>(base(repoId), { method: 'POST' }))
}

export async function renameConversation(repoId: string, id: number, title: string): Promise<ConversationSummary> {
  return toConversation(
    await json<ApiConversation>(`${base(repoId)}/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    }),
  )
}

export async function deleteConversation(repoId: string, id: number): Promise<void> {
  await send(`${base(repoId)}/${id}`, { method: 'DELETE' })
}

/** Deletes this repository's whole chat history; resolves with how many conversations went. */
export async function clearConversations(repoId: string): Promise<number> {
  const response = await send(base(repoId), { method: 'DELETE' })
  return ((await response.json()) as { deleted: number }).deleted
}

export async function getSearchIndex(repoId: string): Promise<ApiSearchIndex> {
  return json<ApiSearchIndex>(`${API_URL}/repositories/${encodeURIComponent(repoId)}/search-index`)
}

/* -------------------------------- Streaming ---------------------------------- */

/**
 * Asks a question and reports progress through `handlers`. Resolves after `done`; throws `ChatError` for
 * every failure (HTTP status, `error` event, or a stream that ended without `done`). Aborting through
 * `signal` rejects with an `AbortError`. Nothing is retried here: a retry is always the user's choice,
 * because every attempt is a billable generation.
 */
export async function streamQuestion(
  repoId: string,
  conversationId: number,
  question: string,
  handlers: StreamHandlers,
  signal: AbortSignal,
): Promise<void> {
  let response: Response
  try {
    response = await fetch(`${base(repoId)}/${conversationId}/messages/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ content: question }),
      signal,
    })
  } catch (err) {
    if (signal.aborted) throw err
    throw new ChatError(`Could not reach the RepoLens API at ${API_URL}. Is the backend running?`, 'network')
  }

  if (!response.ok) {
    const retryAfter = Number(response.headers.get('Retry-After'))
    throw new ChatError(await detailOf(response), httpCode(response.status), retryAfter > 0 ? retryAfter : null)
  }
  if (!response.body) throw new ChatError('The server sent no answer.', 'interrupted')

  try {
    for await (const { event, data } of readSse(response.body)) {
      let payload: unknown
      try {
        payload = JSON.parse(data)
      } catch {
        throw new ChatError('The server sent an unreadable response.', 'interrupted')
      }
      if (event === 'retrieval') {
        const r = payload as ApiRetrieval
        handlers.onRetrieval({ commitSha: r.commit_sha, mode: r.mode, warnings: r.warnings ?? [], sources: r.sources.map(toCitation) })
      } else if (event === 'token') {
        handlers.onToken((payload as { text: string }).text)
      } else if (event === 'error') {
        const e = payload as { code: string; message: string; retry_after: number | null }
        throw new ChatError(e.message, e.code, e.retry_after)
      } else if (event === 'done') {
        const a = payload as ApiAnswer
        const assistant: ChatTurnMessage = {
          ...toMessage(a.assistant_message),
          citations: a.citations.map(toCitation),
          noEvidence: a.answer_status === 'no_evidence',
          warnings: a.warnings,
        }
        handlers.onDone({ user: toMessage(a.user_message), assistant })
        return
      }
    }
  } catch (err) {
    if (err instanceof ChatError || signal.aborted) throw err
    throw new ChatError('The connection was interrupted before the answer finished.', 'interrupted')
  }
  if (signal.aborted) throw new DOMException('Aborted', 'AbortError')
  throw new ChatError('The connection was interrupted before the answer finished.', 'interrupted')
}

function httpCode(status: number): string {
  if (status === 429) return 'rate_limited'
  if (status === 404) return 'not_found'
  if (status === 409) return 'not_ready'
  if (status === 422) return 'invalid'
  if (status === 504) return 'timeout'
  return status >= 500 ? 'unavailable' : 'failed'
}

function formatWait(seconds: number): string {
  if (seconds < 90) return `${Math.ceil(seconds)} seconds`
  if (seconds < 5400) return `${Math.ceil(seconds / 60)} minutes`
  return `${Math.round(seconds / 3600)} hours`
}

/** User-facing text for a failed question. */
export function describeChatError(err: { code: string; message: string; retryAfter: number | null }): string {
  switch (err.code) {
    case 'rate_limited':
      return err.retryAfter
        ? `The AI provider is rate limiting requests. Try again in about ${formatWait(err.retryAfter)}.`
        : 'The AI provider is rate limiting requests. Wait a moment and try again.'
    case 'not_found':
      return 'This conversation no longer exists. Start a new chat.'
    case 'not_ready':
      return err.message
    case 'auth':
    case 'unavailable':
      return err.message || 'Answer generation is temporarily unavailable. Try again in a moment.'
    case 'timeout':
      return 'The answer took too long. Try again, or ask a narrower question.'
    case 'network':
    case 'interrupted':
      return err.message
    default:
      return err.message || 'Something went wrong while answering.'
  }
}
