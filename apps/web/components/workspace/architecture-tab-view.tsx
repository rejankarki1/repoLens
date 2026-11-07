'use client'

import { ArchitectureDiagramView } from '@/components/workspace/architecture/diagram-view'
import { RepositoryGate } from '@/components/workspace/repository-gate'
import type { Repository } from '@/lib/types'

/**
 * The Architecture tab shows only the AI-generated diagram now (product decision: GitDiagram-quality AI
 * diagram, not a dependency-graph/AI-diagram toggle). `?view=diagram` (and any other `view` value) is
 * accepted but has no effect — kept so existing links do not break. The deterministic dependency analyzer,
 * its evidence and backend services are untouched (`services/architecture`, `architecture/explorer.tsx`,
 * `architecture/canvas.tsx`); this tab simply no longer renders that view.
 */
export function ArchitectureTabView({ id }: { id: string }) {
  return <RepositoryGate id={id}>{(repo: Repository) => <ArchitectureDiagramView repo={repo} />}</RepositoryGate>
}
