'use client'

import { useSearchParams } from 'next/navigation'
import { CodeExplorer } from '@/components/workspace/code-explorer'
import { ComingSoon } from '@/components/workspace/coming-soon'
import { RepositoryGate } from '@/components/workspace/repository-gate'
import { RepositoryOverview } from '@/components/workspace/repository-overview'
import { parseTab } from '@/components/workspace/workspace-nav'

export function WorkspaceTabView({ id }: { id: string }) {
  const searchParams = useSearchParams()
  const tab = parseTab(searchParams.get('tab'))

  return (
    <RepositoryGate id={id}>
      {(repo) =>
        tab === 'code' ? (
          <CodeExplorer repo={repo} file={searchParams.get('file')} lines={searchParams.get('lines')} />
        ) : tab === 'ask' ? (
          <ComingSoon title="Ask AI coming soon">
            Questions answered with citations to the exact files and lines are not available yet.
          </ComingSoon>
        ) : (
          <RepositoryOverview repo={repo} />
        )
      }
    </RepositoryGate>
  )
}
