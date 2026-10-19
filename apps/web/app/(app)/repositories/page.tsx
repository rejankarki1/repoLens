import type { Metadata } from 'next'
import { RepositoriesView } from '@/components/repositories-view'

export const metadata: Metadata = { title: 'Repositories' }

export default function RepositoriesPage() {
  return <RepositoriesView />
}
