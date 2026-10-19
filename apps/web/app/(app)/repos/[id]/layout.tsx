import { Suspense } from 'react'
import { RepositoryHeader } from '@/components/workspace/repository-header'
import { WorkspaceNav } from '@/components/workspace/workspace-nav'

export default async function RepositoryLayout({
  children,
  params,
}: {
  children: React.ReactNode
  params: Promise<{ id: string }>
}) {
  const { id } = await params

  return (
    <div className="flex h-full min-h-0 flex-col">
      <RepositoryHeader id={id} />
      <Suspense fallback={<div className="h-10 border-b border-border" />}>
        <WorkspaceNav id={id} />
      </Suspense>
      <div className="min-h-0 flex-1">{children}</div>
    </div>
  )
}
