'use client'

import { ComingSoon } from '@/components/workspace/coming-soon'
import { RepositoryGate } from '@/components/workspace/repository-gate'

export function ArchitectureTabView({ id }: { id: string }) {
  return <RepositoryGate id={id}>{() => (
        <ComingSoon title="Architecture graph coming soon">
          Module dependency analysis is not available yet.
        </ComingSoon>
      )}</RepositoryGate>
}
