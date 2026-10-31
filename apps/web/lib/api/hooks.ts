'use client'

import useSWR from 'swr'
import type { Repository } from '@/lib/types'
import { isActive as analysisActive, STATUS_POLL_MS } from '@/lib/architecture/model'
import { getFile, getFileTree, getRepository, listRepositories } from './client'
import { getArchitecture, getArchitectureNode, getArchitectureStatus, type AnalysisStatus } from './architecture'
import { getSearchIndex, listConversations } from './chat'
import * as api from './mock-client'

/** Queued or being processed by the worker: the status will change on its own, so keep polling. */
const isActive = (r?: Repository) => r?.status === 'queued' || r?.status === 'importing' || r?.status === 'indexing'
const POLL_MS = 1_500

export function useRepositories() {
  return useSWR('repositories', listRepositories, {
    refreshInterval: (data) => (data?.some(isActive) ? POLL_MS : 0),
  })
}

export function useRepository(id: string) {
  return useSWR(['repository', id], () => getRepository(id), {
    refreshInterval: (data) => (isActive(data) ? POLL_MS : 0),
  })
}

/**
 * Repository-scoped resources are fetched whenever a stored snapshot exists (`indexedCommit`), even
 * while a re-ingestion is running or after one failed: the backend keeps serving the last good
 * snapshot. The key includes `lastAnalyzedAt`, which changes on every successful ingestion, so a
 * finished re-ingestion automatically refetches the file list and any open file.
 */
function readyKey(repo: Repository | undefined, ...parts: string[]) {
  return repo?.indexedCommit ? [...parts, repo.id, repo.lastAnalyzedAt ?? ''] : null
}

export function useFileTree(repo: Repository | undefined) {
  return useSWR(readyKey(repo, 'tree'), () => getFileTree(repo!.id))
}

export function useFile(repo: Repository | undefined, path: string | null) {
  return useSWR(path ? readyKey(repo, 'file', path) : null, () => getFile(repo!.id, path!), {
    keepPreviousData: true,
  })
}

export function useInsights(repo: Repository | undefined) {
  return useSWR(readyKey(repo, 'insights'), () => api.getRepositoryInsights(repo!.id))
}

/**
 * Architecture analysis status. Polls only while an analysis is queued or running; the key includes the repository's
 * last-ingested time so a re-ingestion refreshes it.
 */
export function useArchitectureStatus(repo: Repository | undefined) {
  return useSWR(
    repo?.indexedCommit ? ['architecture-status', repo.id, repo.lastAnalyzedAt ?? ''] : null,
    () => getArchitectureStatus(repo!.id),
    { refreshInterval: (data) => (analysisActive(data) ? STATUS_POLL_MS : 0) },
  )
}

/** The key of the stored result: it changes exactly when a new analysis completes, which refetches the graph. */
const resultKey = (status: AnalysisStatus | undefined, ...parts: string[]) =>
  status?.has_result ? [...parts, status.analyzed_commit_sha ?? '', status.analyzed_at ?? ''] : null

/** The last completed graph. Stays available (marked stale by the status) while a newer analysis runs or fails. */
export function useArchitectureGraph(repo: Repository | undefined, status: AnalysisStatus | undefined) {
  return useSWR(
    repo ? resultKey(status, 'architecture-graph', repo.id) : null,
    () => getArchitecture(repo!.id),
    { keepPreviousData: true },
  )
}

/** File-level detail of one module or file, from the same stored result as the graph. */
export function useArchitectureNode(repo: Repository | undefined, nodeId: string | null, status: AnalysisStatus | undefined) {
  return useSWR(
    repo && nodeId ? resultKey(status, 'architecture-node', repo.id, nodeId) : null,
    () => getArchitectureNode(repo!.id, nodeId!),
    { shouldRetryOnError: false }, // a 404 (a file the analysis does not cover) is permanent
  )
}

export function useConversations(repoId: string) {
  return useSWR(['conversations', repoId], () => listConversations(repoId))
}

/** Embedding-index readiness; only informs the UI, chatting works without it (keyword fallback). */
export function useSearchIndex(repo: Repository | undefined) {
  return useSWR(readyKey(repo, 'search-index'), () => getSearchIndex(repo!.id), {
    refreshInterval: (data) => (data?.semantic === 'partial' || data?.semantic === 'not_built' ? 15_000 : 0),
  })
}
