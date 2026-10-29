'use client'

import Link from 'next/link'
import { useState, type ReactNode } from 'react'
import { ChevronRight, CircleHelp, FileCode2, ShieldCheck, TriangleAlert } from 'lucide-react'
import type { Confidence, Evidence, GraphEdge } from '@/lib/api/architecture'
import { codeLink, formatLines, isImportEvidence, kindInfo, RELATIONSHIP_LABEL, type Tone } from '@/lib/architecture/model'
import { cn } from '@/lib/utils'

/* Static class names so Tailwind can see them. The tones reuse the theme's chart colors (light and dark). */
export const TONE_CLASSES: Record<Tone, { bar: string; badge: string; dot: string }> = {
  api: { bar: 'bg-chart-2', badge: 'bg-chart-2/15 text-chart-2', dot: 'bg-chart-2' },
  frontend: { bar: 'bg-chart-1', badge: 'bg-chart-1/15 text-chart-1', dot: 'bg-chart-1' },
  database: { bar: 'bg-chart-3', badge: 'bg-chart-3/15 text-chart-3', dot: 'bg-chart-3' },
  workers: { bar: 'bg-chart-4', badge: 'bg-chart-4/15 text-chart-4', dot: 'bg-chart-4' },
  serverless: { bar: 'bg-chart-5', badge: 'bg-chart-5/15 text-chart-5', dot: 'bg-chart-5' },
  tests: { bar: 'bg-muted-foreground/50', badge: 'bg-muted text-muted-foreground', dot: 'bg-muted-foreground/60' },
  entry: { bar: 'bg-primary', badge: 'bg-primary/10 text-primary', dot: 'bg-primary' },
  plain: { bar: 'bg-border', badge: 'bg-muted text-muted-foreground', dot: 'bg-border' },
}

export function KindBadge({ kind, className }: { kind: string; className?: string }) {
  const { tone, label } = kindInfo(kind)
  if (!label) return null
  return (
    <span className={cn('inline-flex h-5 items-center gap-1 rounded-full px-2 text-[11px] font-medium', TONE_CLASSES[tone].badge, className)}>
      <span className={cn('size-1.5 rounded-full', TONE_CLASSES[tone].dot)} aria-hidden="true" />
      {label}
    </span>
  )
}

/** "Verified": read straight from an import statement. "Inferred": a classification drawn from the lines shown. */
export function ProvenanceBadge({ kind }: { kind: 'verified' | 'inferred' }) {
  return kind === 'verified' ? (
    <span
      className="inline-flex h-5 items-center gap-1 rounded-full bg-success/10 px-2 text-[11px] font-medium text-success"
      title="Found by reading an import statement in the source"
    >
      <ShieldCheck className="size-3" aria-hidden="true" />
      Verified import
    </span>
  ) : (
    <span
      className="inline-flex h-5 items-center gap-1 rounded-full bg-warning/10 px-2 text-[11px] font-medium text-warning"
      title="An interpretation of the code shown, not a relationship between files"
    >
      <CircleHelp className="size-3" aria-hidden="true" />
      Inferred
    </span>
  )
}

export function ConfidenceNote({ confidence }: { confidence: Confidence }) {
  if (confidence === 'verified') return null
  return (
    <span
      className="inline-flex items-center gap-1 text-[11px] text-warning"
      title={
        confidence === 'mixed'
          ? 'Some of the imports behind this dependency were resolved with a heuristic rule'
          : 'The target file was chosen with a heuristic rule (for example an ancestor directory), so it may be wrong'
      }
    >
      <TriangleAlert className="size-3" aria-hidden="true" />
      {confidence === 'mixed' ? 'Partly heuristic' : 'Heuristic target'}
    </span>
  )
}

/** One piece of evidence: the source location (a Code Explorer link) and the exact statement or line. */
export function EvidenceItem({ repoId, evidence, showProvenance = true }: { repoId: string; evidence: Evidence; showProvenance?: boolean }) {
  const range = evidence.kind === 'path' ? null : formatLines(evidence.start_line, evidence.end_line)
  return (
    <li className="flex flex-col gap-1 rounded-md border border-border bg-background/60 p-2">
      <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
        <Link
          href={codeLink(repoId, evidence)}
          className="inline-flex min-w-0 items-center gap-1.5 font-mono text-xs text-primary hover:underline focus-visible:underline"
          title={`Open ${evidence.path}${range ? `, lines ${range}` : ''} in the Code Explorer`}
        >
          <FileCode2 className="size-3.5 shrink-0" aria-hidden="true" />
          <span className="truncate">{evidence.path}</span>
          {range && <span className="shrink-0 text-muted-foreground">:{range}</span>}
        </Link>
        {showProvenance && <ProvenanceBadge kind={isImportEvidence(evidence) ? 'verified' : 'inferred'} />}
      </div>
      <code className="block overflow-x-auto font-mono text-[11px] leading-snug whitespace-pre-wrap break-words text-muted-foreground">
        {evidence.detail}
      </code>
    </li>
  )
}

export function EvidenceList({ repoId, items, showProvenance = true }: { repoId: string; items: Evidence[]; showProvenance?: boolean }) {
  if (items.length === 0) return null
  return (
    <ul className="flex flex-col gap-1.5">
      {items.map((e, i) => (
        <EvidenceItem key={`${e.path}:${e.start_line}:${e.kind}:${i}`} repoId={repoId} evidence={e} showProvenance={showProvenance} />
      ))}
    </ul>
  )
}

/** Why a node/area/technology was classified the way it was. Always labelled as inferred. */
export function ClassificationEvidence({ repoId, items, title = 'Why this classification' }: { repoId: string; items: Evidence[]; title?: string }) {
  if (items.length === 0) return null
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <h3 className="text-xs font-medium text-muted-foreground">{title}</h3>
        <ProvenanceBadge kind="inferred" />
      </div>
      <EvidenceList repoId={repoId} items={items} showProvenance={false} />
    </div>
  )
}

/** The import statements behind backend edges. Aggregated (module) edges show samples, file edges show every statement kept. */
export function EdgeEvidence({
  repoId,
  edge,
  labelOf,
  showEndpoints = true,
  perspective,
}: {
  repoId: string
  edge: GraphEdge
  labelOf: (id: string) => string
  showEndpoints?: boolean
  /** When listing one node's own edges, the node's id: only the other end is named ("imports X" / "imported by X"). */
  perspective?: string
}) {
  const aggregated = edge.basis === 'aggregated'
  const breakdown = Object.entries(edge.breakdown)
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border bg-card p-2.5">
      {showEndpoints && perspective && (
        <p className="flex flex-wrap items-center gap-x-1.5 text-xs">
          <span className="text-muted-foreground" aria-label={edge.source === perspective ? 'imports' : 'imported by'}>
            {edge.source === perspective ? '→' : '←'}
          </span>
          <span className="font-medium break-all">{labelOf(edge.source === perspective ? edge.target : edge.source)}</span>
        </p>
      )}
      {showEndpoints && !perspective && (
        <p className="flex flex-wrap items-center gap-x-1.5 text-xs">
          <span className="font-medium break-all">{labelOf(edge.source)}</span>
          <span className="text-muted-foreground" aria-label="depends on">
            →
          </span>
          <span className="font-medium break-all">{labelOf(edge.target)}</span>
        </p>
      )}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
        {aggregated ? (
          <span>
            {edge.count} file {edge.count === 1 ? 'dependency' : 'dependencies'}
            {breakdown.length > 0 && ` · ${breakdown.map(([k, v]) => `${v} ${RELATIONSHIP_LABEL[k] ?? k}`).join(', ')}`}
          </span>
        ) : (
          <span>
            {RELATIONSHIP_LABEL[edge.relationship] ?? edge.relationship}
            {edge.count > 1 && ` · ${edge.count} statements`}
          </span>
        )}
        {!aggregated && <span>· resolved by {edge.resolution.replace(/_/g, ' ')}</span>}
        <ConfidenceNote confidence={edge.confidence} />
      </div>
      <EvidenceList repoId={repoId} items={edge.evidence} />
      {edge.evidence_truncated && (
        <p className="text-[11px] text-muted-foreground">
          Showing {edge.evidence.length} of {edge.count} {aggregated ? 'file dependencies' : 'import statements'}.
        </p>
      )}
    </div>
  )
}

/* ------------------------------ Small primitives ------------------------------ */

export function Section({
  title,
  count,
  defaultOpen = true,
  children,
}: {
  title: string
  count?: number | string
  defaultOpen?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <section className="flex flex-col gap-2">
      <h2>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="flex w-full items-center gap-1.5 rounded text-left text-xs font-semibold tracking-wide text-muted-foreground uppercase hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
        >
          <ChevronRight className={cn('size-3.5 transition-transform', open && 'rotate-90')} aria-hidden="true" />
          {title}
          {count !== undefined && <span className="font-normal tabular-nums">({count})</span>}
        </button>
      </h2>
      {open && <div className="flex flex-col gap-2">{children}</div>}
    </section>
  )
}

export function Stat({ label, value, tone }: { label: string; value: number | string; tone?: 'warning' | 'danger' }) {
  return (
    <div className="flex flex-col rounded-lg border border-border bg-card px-3 py-2">
      <span className="text-[11px] text-muted-foreground">{label}</span>
      <span
        className={cn(
          'text-lg font-semibold tabular-nums tracking-tight',
          tone === 'warning' && 'text-warning',
          tone === 'danger' && 'text-destructive',
        )}
      >
        {value}
      </span>
    </div>
  )
}
