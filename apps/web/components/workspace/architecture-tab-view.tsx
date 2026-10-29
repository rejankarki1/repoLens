'use client'

import { ArchitectureExplorer } from '@/components/workspace/architecture/explorer'
import { RepositoryGate } from '@/components/workspace/repository-gate'

export function ArchitectureTabView({ id }: { id: string }) {
  return <RepositoryGate id={id}>{(repo) => <ArchitectureExplorer repo={repo} />}</RepositoryGate>
}
