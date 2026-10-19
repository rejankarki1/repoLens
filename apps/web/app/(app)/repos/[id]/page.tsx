import { Suspense } from 'react'
import { WorkspaceTabView } from '@/components/workspace/workspace-tab-view'

export default async function RepositoryPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params

  return (
    <Suspense>
      <WorkspaceTabView id={id} />
    </Suspense>
  )
}
