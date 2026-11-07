import { ApiError, request } from '@/lib/api/client'
import type { ArchitectureDiagram } from '@/lib/architecture-diagram/types'

/* Types mirror apps/api/schemas/architecture_diagram.py's DiagramStatus/DiagramResponse/DiagramQueueResponse
 * field for field, the same convention `lib/api/architecture.ts` uses for the deterministic graph. */

export type DiagramAttemptStatus = 'none' | 'queued' | 'running' | 'completed' | 'failed'

export interface DiagramStatus {
  repository_id: number
  status: DiagramAttemptStatus // the latest generation attempt
  freshness: 'none' | 'current' | 'stale' // of the stored result
  current_commit_sha: string | null
  generated_commit_sha: string | null
  target_commit_sha: string | null
  generated_at: string | null
  requested_at: string | null
  error: string | null
  has_result: boolean
  model: string | null
}

export interface DiagramResponse {
  status: DiagramStatus
  diagram: ArchitectureDiagram
}

export interface DiagramQueueResponse {
  status: DiagramStatus
  enqueued: boolean
}

const base = (repoId: string) => `/repositories/${encodeURIComponent(repoId)}/architecture-diagram`

export const getArchitectureDiagramStatus = (repoId: string) => request<DiagramStatus>(`${base(repoId)}/status`)

/** The stored diagram, or null while none has completed (the API answers 404 until then). */
export async function getArchitectureDiagram(repoId: string): Promise<DiagramResponse | null> {
  try {
    return await request<DiagramResponse>(base(repoId))
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null
    throw err
  }
}

/**
 * Asks the backend to generate an AI architecture diagram for the repository's current commit. Idempotent
 * server-side: while a generation of the same commit is queued or running, or the stored result is current
 * (without `force`), nothing new is queued — and so no AI provider call is spent.
 */
export const requestArchitectureDiagram = (repoId: string, force = false) =>
  request<DiagramQueueResponse>(`${base(repoId)}${force ? '?force=true' : ''}`, { method: 'POST' })
