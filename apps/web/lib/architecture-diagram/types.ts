/**
 * Architecture Diagram V2: the provider-independent validated graph produced by the backend
 * (`apps/api/schemas/architecture_diagram.py::ArchitectureDiagram`). Mirrors that schema field for field;
 * nothing here is computed on the client — `evidence_basis`, `confidence` and `evidence` are exactly what
 * the backend sealed them to.
 */

export type ComponentKind =
  | 'service' | 'api' | 'worker' | 'job' | 'ui' | 'page' | 'database' | 'store'
  | 'external_service' | 'library' | 'cli' | 'pipeline' | 'other'

export type RelationshipKind = 'imports' | 'sync_call' | 'async_call' | 'queue' | 'db_read' | 'db_write' | 'renders' | 'other'

export type Confidence = 'verified' | 'inferred'
export type EvidenceBasis = 'verified_paths' | 'inferred'

export interface DiagramEvidence {
  path: string
  start_line: number
  end_line: number
  detail: string
}

export interface DiagramGroup {
  id: string
  label: string
  description: string
}

export interface DiagramComponent {
  id: string
  group_id: string | null
  label: string
  description: string
  kind: ComponentKind
  source_paths: string[]
  evidence_basis: EvidenceBasis
}

export interface DiagramRelationship {
  id: string
  source: string
  target: string
  label: string
  kind: RelationshipKind
  confidence: Confidence
  evidence: DiagramEvidence[]
}

export interface ArchitectureDiagram {
  repository_commit_sha: string
  generator_version: number
  groups: DiagramGroup[]
  components: DiagramComponent[]
  relationships: DiagramRelationship[]
  warnings: string[]
}
