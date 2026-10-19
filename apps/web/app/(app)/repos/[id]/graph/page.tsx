import { ArchitectureTabView } from '@/components/workspace/architecture-tab-view'

export default async function ArchitecturePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  return <ArchitectureTabView id={id} />
}
