/**
 * Which screen the Architecture tab shows, decided from the status query and the diagram query together. A
 * pure function so the awkward combinations are tested: a status that says "there is a diagram" while the
 * diagram itself is still downloading must read as loading, never as "no diagram yet"; a status refetch that
 * fails while a diagram is on screen must keep the diagram.
 */
import type { DiagramResponse, DiagramStatus } from '@/lib/api/architecture-diagram'
import type { ArchitectureDiagram } from '@/lib/architecture-diagram/types'
// A relative `.ts` import (not the `@/` alias) so Node's test runner can load this module without a bundler.
import { describeFailure, loadView, type QueryState } from '../api/request-state.ts'

export type DiagramView =
  /** The status is loading, or the status says a diagram exists and it is still being fetched. */
  | { kind: 'loading' }
  /** No status could be loaded at all. `retryable`: a network or server failure, worth a "Try again". */
  | { kind: 'status-error'; message: string; retryable: boolean }
  /** A diagram to draw. `refreshFailed`: the latest status poll failed, but the status shown is still the last good one. */
  | { kind: 'diagram'; status: DiagramStatus; diagram: ArchitectureDiagram; refreshFailed: boolean }
  /** The status promises a diagram, but fetching it failed and nothing older is cached. */
  | { kind: 'diagram-error'; status: DiagramStatus; message: string; retryable: boolean }
  /** A generation is queued or running and there is no earlier diagram to show meanwhile. */
  | { kind: 'pending'; status: DiagramStatus }
  /** The last generation failed and there is no earlier diagram. */
  | { kind: 'failed'; status: DiagramStatus }
  /** Nothing has been generated for this repository. */
  | { kind: 'none'; status: DiagramStatus }

export function diagramView(
  statusQuery: QueryState<DiagramStatus>,
  diagramQuery: QueryState<DiagramResponse | null>,
): DiagramView {
  const status = loadView(statusQuery)
  if (status.kind === 'loading') return { kind: 'loading' }
  if (status.kind === 'error') {
    return { kind: 'status-error', message: describeFailure(status.error, 'Could not load the diagram status.'), retryable: status.transient }
  }
  const s = status.data
  const diagram = diagramQuery.data?.diagram
  if (diagram) return { kind: 'diagram', status: s, diagram, refreshFailed: status.refreshFailed }

  if (s.has_result) {
    // The status says a diagram is stored. `data === null` means the API answered 404 for it after all (it was
    // deleted between the two requests): fall through to the plain states below rather than wait forever.
    const result = loadView(diagramQuery)
    if (result.kind === 'loading') return { kind: 'loading' }
    if (result.kind === 'error') {
      return { kind: 'diagram-error', status: s, message: describeFailure(result.error, 'Could not load the diagram.'), retryable: result.transient }
    }
  }
  if (s.status === 'queued' || s.status === 'running') return { kind: 'pending', status: s }
  if (s.status === 'failed') return { kind: 'failed', status: s }
  return { kind: 'none', status: s }
}
