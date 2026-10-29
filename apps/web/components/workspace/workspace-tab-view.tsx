'use client'

import { useSearchParams } from 'next/navigation'
import { CodeExplorer } from '@/components/workspace/code-explorer'
import { ChatPanel } from '@/components/workspace/chat/chat-panel'
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
          <ChatPanel key={repo.id} repo={repo} />
        ) : (
          <RepositoryOverview repo={repo} />
        )
      }
    </RepositoryGate>
  )
}
