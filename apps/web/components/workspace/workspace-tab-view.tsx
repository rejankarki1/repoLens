'use client'

import { useSearchParams } from 'next/navigation'
import { CodeExplorer } from '@/components/workspace/code-explorer'
import { ChatPanel } from '@/components/workspace/chat/chat-panel'
import { RepositoryGate } from '@/components/workspace/repository-gate'
import { RepositoryOverview } from '@/components/workspace/repository-overview'
import { parseTab } from '@/components/workspace/workspace-nav'

/** The conversation named in `?c=`, if it is a positive whole number. */
function parseConversationId(value: string | null): number | null {
  return value && /^[1-9]\d{0,9}$/.test(value) ? Number(value) : null
}

export function WorkspaceTabView({ id }: { id: string }) {
  const searchParams = useSearchParams()
  const tab = parseTab(searchParams.get('tab'))

  return (
    <RepositoryGate id={id}>
      {(repo) =>
        tab === 'code' ? (
          <CodeExplorer repo={repo} file={searchParams.get('file')} lines={searchParams.get('lines')} />
        ) : tab === 'ask' ? (
          <ChatPanel key={repo.id} repo={repo} initialConversationId={parseConversationId(searchParams.get('c'))} />
        ) : (
          <RepositoryOverview repo={repo} />
        )
      }
    </RepositoryGate>
  )
}
