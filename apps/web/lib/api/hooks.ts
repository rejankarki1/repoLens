'use client'

import useSWR from 'swr'
import type { Repository } from '@/lib/types'
import { getFile, getFileTree, getRepository, isIngestionRunning, listRepositories } from './client'
import * as api from './mock-client'

const isInProgress = (r?: Repository) => r?.status === 'importing' || r?.status === 'indexing'
const POLL_MS = 1_500

export function useRepositories() {
  return useSWR('repositories', listRepositories, {
    refreshInterval: (data) =>
      data?.some((r) => isInProgress(r) || isIngestionRunning(r.id)) ? POLL_MS : 0,
  })
}

export function useRepository(id: string) {
  return useSWR(['repository', id], () => getRepository(id), {
    refreshInterval: (data) => (isInProgress(data) || isIngestionRunning(id) ? POLL_MS : 0),
  })
}

/** Repository-scoped resources are only fetched once indexing has completed. */
function readyKey(repo: Repository | undefined, ...parts: string[]) {
  return repo?.status === 'completed' ? [...parts, repo.id, repo.lastAnalyzedAt ?? ''] : null
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

export function useDependencyGraph(repo: Repository | undefined) {
  return useSWR(readyKey(repo, 'graph'), () => api.getDependencyGraph(repo!.id))
}

export function useConversations(repoId: string) {
  return useSWR(['conversations', repoId], () => api.listConversations(repoId))
}
