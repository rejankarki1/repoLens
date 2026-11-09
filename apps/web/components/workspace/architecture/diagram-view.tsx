'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import { useTheme } from 'next-themes'
import {
  AlertCircle, CheckCircle2, ChevronDown, Clock, ExternalLink, Loader2, Map as MapIcon, Maximize, Minimize2, Expand,
  RefreshCw, Sparkles, TriangleAlert, X, ZoomIn, ZoomOut,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { requestArchitectureDiagram, type DiagramStatus } from '@/lib/api/architecture-diagram'
import { useArchitectureDiagram, useArchitectureDiagramStatus } from '@/lib/api/hooks'
import { fileLink, shortSha } from '@/lib/architecture/model'
import { DIAGRAM_CLICK_CALLBACK, diagramToMermaid, legendEntries } from '@/lib/architecture-diagram/mermaid'
import type { ArchitectureDiagram, DiagramComponent } from '@/lib/architecture-diagram/types'
import {
  adaptiveMinScale, centeredTransform, clampedFitScale, connectedToNode, fitScaleFor, minimapPointToTransform, panBy, svgSize,
  viewportRectInMinimap, wheelIntent, zoomTowardPoint, type Transform,
} from '@/lib/architecture-diagram/viewport'
import { formatRelativeTime } from '@/lib/repo-utils'
import type { Repository } from '@/lib/types'
import { cn } from '@/lib/utils'

function messageOf(err: unknown): string {
  return err instanceof Error ? err.message : 'Something went wrong.'
}

/**
 * AI-identified functional architecture (Phase 6: the real pipeline, not a fixture): reads the generated
 * diagram from `/architecture-diagram`, compiles it to Mermaid with `diagramToMermaid`, and renders it with
 * Mermaid.js. A sibling, not a replacement, of the deterministic import-graph explorer
 * (`architecture/explorer.tsx`) — the two answer different questions about the same repository.
 *
 * Generation is never automatic (it spends a real AI call): the user requests it explicitly, same posture
 * as the rest of this codebase takes toward paid-provider calls.
 */
export function ArchitectureDiagramView({ repo }: { repo: Repository }) {
  const statusQuery = useArchitectureDiagramStatus(repo)
  const diagramQuery = useArchitectureDiagram(repo, statusQuery.data)
  const [requestError, setRequestError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  // Generating is a member action (spends the workspace's AI budget); guests only read what already exists.
  const canGenerate = repo.viewerAccess === 'member'
  const { mutate: refreshStatus } = statusQuery

  const request = useCallback(
    async (force: boolean) => {
      setSubmitting(true)
      setRequestError(null)
      try {
        await requestArchitectureDiagram(repo.id, force)
        await refreshStatus()
      } catch (err) {
        setRequestError(messageOf(err))
      } finally {
        setSubmitting(false)
      }
    },
    [repo.id, refreshStatus],
  )

  if (statusQuery.isLoading) return <DiagramSkeleton />
  if (statusQuery.error || !statusQuery.data) {
    return <CenteredMessage icon={<TriangleAlert className="size-5 text-warning" aria-hidden="true" />} message={messageOf(statusQuery.error)} />
  }
  const status = statusQuery.data
  const diagram = diagramQuery.data?.diagram

  if (diagram) {
    return (
      <DiagramCanvas
        repo={repo}
        diagram={diagram}
        status={status}
        requestError={requestError}
        onRegenerate={canGenerate ? () => void request(status.freshness === 'current') : undefined}
        regenerating={submitting}
      />
    )
  }
  if (status.status === 'queued' || status.status === 'running') return <DiagramPendingState status={status} />
  if (status.status === 'failed') {
    return (
      <GenerateState
        title="Diagram generation failed"
        message={status.error ?? 'Something went wrong while generating the diagram.'}
        icon={<AlertCircle className="size-5 text-destructive" aria-hidden="true" />}
        canGenerate={canGenerate}
        label="Try again"
        onGenerate={() => void request(true)}
        submitting={submitting}
        requestError={requestError}
      />
    )
  }
  return (
    <GenerateState
      title="No AI architecture diagram yet"
      message="Generate a diagram that groups this repository's real source files into the functional components a developer would actually point to — authentication, routes, business logic, persistence, integrations — with real and AI-inferred relationships clearly distinguished."
      icon={<Sparkles className="size-5 text-primary" aria-hidden="true" />}
      canGenerate={canGenerate}
      label="Generate diagram"
      onGenerate={() => void request(false)}
      submitting={submitting}
      requestError={requestError}
    />
  )
}

function DiagramSkeleton() {
  return (
    <div className="flex h-full flex-col gap-3 p-6" aria-busy="true" aria-label="Loading architecture diagram">
      <Skeleton className="h-8 w-72" />
      <Skeleton className="min-h-64 flex-1" />
    </div>
  )
}

function CenteredMessage({ message, detail, icon }: { message: string; detail?: string; icon?: React.ReactNode }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-sm text-muted-foreground">
      {icon}
      <p>{message}</p>
      {detail && <p className="max-w-md text-xs">{detail}</p>}
    </div>
  )
}

/** No diagram exists yet, or the last attempt failed: an explicit call to action, never an automatic request. */
function GenerateState({
  title, message, icon, canGenerate, label, onGenerate, submitting, requestError,
}: {
  title: string
  message: string
  icon: React.ReactNode
  canGenerate: boolean
  label: string
  onGenerate: () => void
  submitting: boolean
  requestError: string | null
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
      <div className="flex size-10 items-center justify-center rounded-lg bg-muted">{icon}</div>
      <p className="text-sm font-medium">{title}</p>
      <p className="max-w-md text-sm text-muted-foreground">{message}</p>
      {canGenerate ? (
        <Button size="sm" onClick={onGenerate} disabled={submitting}>
          {submitting ? <Loader2 className="animate-spin" aria-hidden="true" /> : <Sparkles aria-hidden="true" />}
          {label}
        </Button>
      ) : (
        <p className="text-xs text-muted-foreground">Sign in as a member of this repository to generate a diagram.</p>
      )}
      {requestError && <p role="alert" className="max-w-md text-xs text-destructive">{requestError}</p>}
    </div>
  )
}

function DiagramPendingState({ status }: { status: DiagramStatus }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
      <Loader2 className="size-5 animate-spin text-primary" aria-hidden="true" />
      <p className="text-sm font-medium">{status.status === 'queued' ? 'Waiting for generation to start' : 'Generating the diagram'}</p>
      <p role="status" className="max-w-md text-sm text-muted-foreground">
        {status.status === 'queued'
          ? 'The request is queued and runs in the background. This page updates on its own when it is done.'
          : 'The AI is identifying functional components from the repository’s structure and source. This usually takes under a minute.'}
      </p>
    </div>
  )
}

/** Notices that sit above an existing diagram: stale, regenerating, failed. Mirrors `architecture/status.tsx`'s StatusBanners. */
function DiagramBanners({ status, requestError }: { status: DiagramStatus; requestError: string | null }) {
  const active = status.status === 'queued' || status.status === 'running'
  const stale = status.has_result && status.freshness === 'stale'
  const items: { key: string; tone: 'info' | 'warning' | 'danger'; text: string }[] = []

  if (requestError) items.push({ key: 'request', tone: 'danger', text: `Could not start regeneration: ${requestError}` })
  if (status.status === 'failed' && status.has_result) {
    items.push({
      key: 'failed',
      tone: 'danger',
      text: `The latest generation failed${status.error ? `: ${status.error.replace(/[.\s]+$/, '')}` : ''}. The diagram below is the last successful one, for commit ${shortSha(status.generated_commit_sha)}.`,
    })
  } else if (stale && active) {
    items.push({
      key: 'updating',
      tone: 'info',
      text: `Regenerating for commit ${shortSha(status.current_commit_sha)}. Until it finishes, this is the diagram for commit ${shortSha(status.generated_commit_sha)}.`,
    })
  } else if (stale) {
    items.push({
      key: 'stale',
      tone: 'warning',
      text: `This diagram is for commit ${shortSha(status.generated_commit_sha)}, but the repository is now at ${shortSha(status.current_commit_sha)}. Regenerate to update it.`,
    })
  } else if (active && status.has_result) {
    items.push({ key: 'refreshing', tone: 'info', text: 'Regenerating the diagram. This diagram stays available until it finishes.' })
  }
  if (items.length === 0) return null
  return (
    <div className="flex flex-col">
      {items.map((b) => (
        <p
          key={b.key}
          role={b.tone === 'danger' ? 'alert' : 'status'}
          className={cn(
            'flex items-start gap-2 border-b px-3 py-2 text-xs sm:px-5',
            b.tone === 'danger' && 'border-destructive/40 bg-destructive/10 text-destructive',
            b.tone === 'warning' && 'border-warning/40 bg-warning/10 text-warning',
            b.tone === 'info' && 'border-border bg-muted/40 text-muted-foreground',
          )}
        >
          {b.tone === 'info' ? (
            <Loader2 className="mt-0.5 size-3.5 shrink-0 animate-spin" aria-hidden="true" />
          ) : (
            <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          )}
          {b.text}
        </p>
      ))}
    </div>
  )
}

const MIN_SCALE = 0.1
const MAX_SCALE = 3
const ZOOM_STEP = 1.25
// Below this, fitting the whole graph would make labels unreadable: stay legible and let the user pan.
const READABLE_SCALE = 0.55
const FIT_MAX_SCALE = 1.5
const FIT_PADDING = 32
const DRAG_THRESHOLD = 4
// Below this zoom level, edge labels and the secondary (filename) node line are hidden: at a glance over a
// large, dense graph they are unreadable anyway and only add visual noise; they return as soon as the user
// zooms in enough to read them.
const DECLUTTER_SCALE = 0.75
const MINIMAP_SIZE = { w: 176, h: 128 }
const WHEEL_ZOOM_SENSITIVITY = 0.01
const DOUBLE_CLICK_MS = 400
const NODE_FOCUS_PADDING = 56
const NODE_FOCUS_MAX_SCALE = 2

type ContentBox = { x: number; y: number; w: number; h: number }

function DiagramCanvas({
  repo, diagram, status, requestError, onRegenerate, regenerating,
}: {
  repo: Repository
  diagram: ArchitectureDiagram
  status: DiagramStatus
  requestError: string | null
  onRegenerate?: () => void
  regenerating: boolean
}) {
  const { resolvedTheme } = useTheme()
  const theme = resolvedTheme === 'dark' ? 'dark' : 'light'
  const viewportRef = useRef<HTMLDivElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const minimapRef = useRef<HTMLDivElement>(null)
  const transformRef = useRef<Transform>({ x: 0, y: 0, scale: 1 })
  const [transform, setTransformState] = useState<Transform>({ x: 0, y: 0, scale: 1 })
  const [fitted, setFitted] = useState(false)
  const [animate, setAnimate] = useState(false)
  const [renderError, setRenderError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [focusMode, setFocusMode] = useState(false)
  const [showMinimap, setShowMinimap] = useState(true)
  const [minimapSvg, setMinimapSvg] = useState<string | null>(null)
  const [contentBox, setContentBox] = useState<ContentBox | null>(null)
  const [viewportSize, setViewportSize] = useState({ w: 0, h: 0 })
  const userMoved = useRef(false) // once the user zooms or pans, a resize no longer re-fits behind their back
  const pointers = useRef(new Map<number, { x: number; y: number }>())
  const gesture = useRef<{ x: number; y: number; origin: Transform; dist: number; moved: boolean } | null>(null)
  const suppressClick = useRef(false)
  const lastClick = useRef<{ id: string; time: number } | null>(null)

  const componentsById = useMemo(() => new Map(diagram.components.map((c) => [c.id, c])), [diagram])
  const selected = selectedId ? componentsById.get(selectedId) ?? null : null
  const lr = useMemo(() => diagramToMermaid(diagram, theme, 'LR'), [diagram, theme])
  const td = useMemo(() => diagramToMermaid(diagram, theme, 'TD'), [diagram, theme])
  // `nodeIdToComponentId` and `edgeEndpoints` depend only on the deterministic order components/relationships
  // are iterated in, never on direction, so lr's and td's are always identical; either can be used regardless
  // of which orientation actually gets rendered (same reasoning the original code already relied on below).
  const focused = selectedId ? connectedToNode(lr.edgeEndpoints, selectedId) : null

  const setTransform = useCallback((next: Transform, smooth = false) => {
    transformRef.current = next
    setAnimate(smooth)
    setTransformState(next)
  }, [])

  const fitToView = useCallback(
    (smooth = false) => {
      const svg = containerRef.current?.querySelector<SVGSVGElement>('svg')
      const viewport = viewportRef.current
      if (!svg || !viewport) return
      const w = Number(svg.getAttribute('width'))
      const h = Number(svg.getAttribute('height'))
      const vw = viewport.clientWidth
      const vh = viewport.clientHeight
      if (!w || !h || !vw || !vh) return
      // Read the origin straight from the live SVG's viewBox rather than the `contentBox` state, which
      // could still be stale (from before this render) the first time this runs after a fresh render.
      const vb = svg.viewBox.baseVal
      const origin = { x: vb?.x ?? 0, y: vb?.y ?? 0 }
      // Fit if legible; otherwise stay readable and let the user pan the overflowing axis. The graph's own
      // aspect ratio always drives this — nothing here assumes or forces a fixed frame shape.
      const scale = clampedFitScale({ w, h }, vw, vh, { readableScale: READABLE_SCALE, maxScale: FIT_MAX_SCALE, padding: FIT_PADDING })
      const placement = centeredTransform({ x: origin.x, y: origin.y, width: w, height: h }, origin, { w: vw, h: vh }, scale, FIT_PADDING)
      userMoved.current = false
      setTransform({ ...placement, scale }, smooth)
      setFitted(true)
    },
    [setTransform],
  )

  /** Double-clicking a node smoothly centers and zooms in on it (capped at `NODE_FOCUS_MAX_SCALE`, so a
   * tiny node does not balloon to an unreasonable size). `nodeId` is the sanitized mermaid id, matching
   * the `flowchart-<id>-<n>` element the renderer gave it. */
  const focusOnNode = useCallback(
    (nodeId: string) => {
      const svg = containerRef.current?.querySelector<SVGSVGElement>('svg')
      const viewport = viewportRef.current
      const el = containerRef.current?.querySelector<SVGGraphicsElement>(`[id^="flowchart-${nodeId}-"]`)
      if (!svg || !viewport || !el) return
      let bb: DOMRect
      try {
        bb = el.getBBox()
      } catch {
        return
      }
      if (bb.width <= 0 || bb.height <= 0) return
      const vw = viewport.clientWidth
      const vh = viewport.clientHeight
      const vb = svg.viewBox.baseVal
      const origin = { x: vb?.x ?? 0, y: vb?.y ?? 0 }
      const scale = clampedFitScale({ w: bb.width, h: bb.height }, vw, vh, { readableScale: 0.1, maxScale: NODE_FOCUS_MAX_SCALE, padding: NODE_FOCUS_PADDING })
      const placement = centeredTransform({ x: bb.x, y: bb.y, width: bb.width, height: bb.height }, origin, { w: vw, h: vh }, scale, NODE_FOCUS_PADDING)
      userMoved.current = true
      setTransform({ ...placement, scale }, true)
    },
    [setTransform],
  )

  // The click handler mermaid's `click ... call` directive invokes; see diagramToMermaid's doc comment
  // for why `securityLevel: "loose"` is safe given this codebase's label-escaping discipline. A single
  // click toggles focus (highlighting its neighbors, dimming the rest) and opens the side panel, as
  // before; a second click on the *same* node within DOUBLE_CLICK_MS is treated as a double-click and
  // additionally smoothly zooms in on it (mermaid's own `click` directive does not distinguish single
  // from double clicks, so this is tracked here rather than relying on a native `dblclick` listener).
  const nodeIdToComponentId = lr.nodeIdToComponentId
  useEffect(() => {
    const scope = window as unknown as Record<string, (nodeId: string) => void>
    scope[DIAGRAM_CLICK_CALLBACK] = (nodeId: string) => {
      if (suppressClick.current) return // the pointer was dragging the canvas, not clicking a node
      const componentId = nodeIdToComponentId[nodeId]
      if (!componentId) return
      const now = performance.now()
      const last = lastClick.current
      if (last && last.id === componentId && now - last.time < DOUBLE_CLICK_MS) {
        lastClick.current = null
        setSelectedId(componentId)
        focusOnNode(nodeId)
        return
      }
      lastClick.current = { id: componentId, time: now }
      setSelectedId((current) => (current === componentId ? null : componentId))
    }
    return () => {
      delete scope[DIAGRAM_CLICK_CALLBACK]
    }
  }, [nodeIdToComponentId, focusOnNode])

  useEffect(() => {
    let cancelled = false
    async function render() {
      setRenderError(null)
      setFitted(false)
      try {
        const mermaid = (await import('mermaid')).default
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: 'loose',
          theme: 'base',
          fontFamily: 'inherit',
          flowchart: {
            curve: 'basis', // smooth, routed bends rather than mermaid's default sharp linear joins
            nodeSpacing: 44,
            rankSpacing: 64,
            padding: 14,
          },
        })
        const tag = Math.random().toString(36).slice(2)
        const [a, b] = await Promise.all([
          mermaid.render(`arch-lr-${tag}`, lr.text),
          mermaid.render(`arch-td-${tag}`, td.text),
        ])
        if (cancelled || !containerRef.current) return
        // Pick the orientation that fills the actual canvas better (higher fit scale = larger labels), preferring
        // the wide left-to-right layout unless top-down is clearly better. Deterministic: same size, same choice.
        const viewport = viewportRef.current
        const sa = svgSize(a.svg)
        const sb = svgSize(b.svg)
        let chosen = a
        if (viewport && sa && sb) {
          const fa = fitScaleFor(sa, viewport.clientWidth, viewport.clientHeight)
          const fb = fitScaleFor(sb, viewport.clientWidth, viewport.clientHeight)
          if (fb > fa * 1.15) chosen = b
        }
        containerRef.current.innerHTML = chosen.svg
        // Mermaid's SVG is `width="100%"`, which collapses inside this absolutely positioned, transform-scaled
        // wrapper. Pin it to the rendered content's real bounds (not the padded viewBox) so Fit to view and
        // zoom operate on the actual diagram, with no unused whitespace around it.
        const svgEl = containerRef.current.querySelector('svg')
        if (svgEl) {
          const pad = 12
          let box: { x: number; y: number; width: number; height: number } | null = null
          try {
            const bb = svgEl.getBBox()
            if (bb.width > 0 && bb.height > 0) box = { x: bb.x - pad, y: bb.y - pad, width: bb.width + pad * 2, height: bb.height + pad * 2 }
          } catch {
            box = null
          }
          const vb = svgEl.viewBox.baseVal
          box ??= vb && vb.width > 0 ? { x: vb.x, y: vb.y, width: vb.width, height: vb.height } : null
          if (box) {
            svgEl.setAttribute('viewBox', `${box.x} ${box.y} ${box.width} ${box.height}`)
            svgEl.setAttribute('width', String(box.width))
            svgEl.setAttribute('height', String(box.height))
            svgEl.style.maxWidth = 'none'
            setContentBox({ x: box.x, y: box.y, w: box.width, h: box.height })
          }
        }
        chosen.bindFunctions?.(containerRef.current)
        // A second, non-interactive copy for the minimap: same trusted, already-escaped SVG markup the
        // main canvas just rendered (see diagramToMermaid's doc comment on label escaping), just displayed
        // smaller. No click bindings are attached to it, so it is inert except for our own drag/click-to-pan.
        setMinimapSvg(containerRef.current.innerHTML)
        fitToView()
      } catch (err) {
        if (!cancelled) setRenderError(messageOf(err))
      }
    }
    void render()
    return () => {
      cancelled = true
    }
  }, [lr, td, fitToView])

  // Keep the fitted view fitted when the canvas is resized, unless the user has taken over the view.
  // Also tracks the viewport's size in state (for the minimap) - a ref cannot be read during render.
  useEffect(() => {
    const viewport = viewportRef.current
    if (!viewport) return
    setViewportSize({ w: viewport.clientWidth, h: viewport.clientHeight })
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => {
      setViewportSize({ w: viewport.clientWidth, h: viewport.clientHeight })
      if (!userMoved.current && containerRef.current?.querySelector('svg')) fitToView()
    })
    observer.observe(viewport)
    return () => observer.disconnect()
  }, [fitToView])

  // Entering or leaving focus mode changes the available space outright (a user action, not an organic
  // resize), so it always re-fits, even if the user had already taken over panning/zooming.
  useEffect(() => {
    const id = requestAnimationFrame(() => {
      userMoved.current = false
      fitToView(true)
    })
    return () => cancelAnimationFrame(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentionally only on focusMode changes
  }, [focusMode])

  useEffect(() => {
    if (!focusMode) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setFocusMode(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [focusMode])

  /** Zoom by `factor` keeping the point (px, py), in viewport coordinates, fixed on screen. The lower
   * bound adapts to the graph's own size: a huge graph can always be zoomed out far enough to see all of
   * it, while a small one never zooms out to an illegible speck past `MIN_SCALE`. */
  const zoomAt = useCallback(
    (factor: number, px: number, py: number, smooth = false) => {
      const t = transformRef.current
      const viewport = viewportRef.current
      const svg = containerRef.current?.querySelector('svg')
      const w = svg ? Number(svg.getAttribute('width')) : 0
      const h = svg ? Number(svg.getAttribute('height')) : 0
      const minScale = viewport && w && h ? adaptiveMinScale({ w, h }, viewport.clientWidth, viewport.clientHeight, MIN_SCALE) : MIN_SCALE
      const next = zoomTowardPoint(t, factor, { x: px, y: py }, minScale, MAX_SCALE)
      if (next === t) return
      userMoved.current = true
      setTransform(next, smooth)
    },
    [setTransform],
  )

  const zoomButton = useCallback(
    (factor: number) => {
      const viewport = viewportRef.current
      if (viewport) zoomAt(factor, viewport.clientWidth / 2, viewport.clientHeight / 2, true)
    },
    [zoomAt],
  )

  // Wheel / trackpad: non-passive native listener so the page never scrolls under the canvas (and so the
  // gesture never leaks into it either). The root cause this fixes: every wheel event used to zoom, just
  // more gently without ctrlKey - a two-finger trackpad pan and an ordinary mouse wheel both arrive as
  // plain (non-ctrl, non-meta) wheel events, so neither one ever had a code path that actually panned.
  //
  // - ctrlKey (a trackpad pinch is reported this way by the browser, indistinguishable from a real
  //   Ctrl+wheel) or metaKey (Cmd+wheel): zoom toward the cursor.
  // - otherwise: pan by the raw delta. A two-finger trackpad swipe reports real deltaX and deltaY, so it
  //   pans in the swiped direction; an ordinary mouse wheel reports only deltaY, so it pans vertically.
  //   Shift forces horizontal panning, falling back to deltaY as the horizontal source for input devices
  //   that do not remap it to deltaX themselves when Shift is held.
  useEffect(() => {
    const viewport = viewportRef.current
    if (!viewport) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const intent = wheelIntent(e)
      if (intent.type === 'zoom') {
        const rect = viewport.getBoundingClientRect()
        zoomAt(Math.exp(-intent.delta * WHEEL_ZOOM_SENSITIVITY), e.clientX - rect.left, e.clientY - rect.top)
        return
      }
      userMoved.current = true
      setTransform(panBy(transformRef.current, -intent.dx, -intent.dy))
    }
    viewport.addEventListener('wheel', onWheel, { passive: false })
    return () => viewport.removeEventListener('wheel', onWheel)
  }, [zoomAt, setTransform])

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    // Prevents a click-drag that starts over a node's SVG text from turning into a native text selection
    // (it does not stop the eventual `click` event mermaid's node bindings rely on).
    e.preventDefault()
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    const pts = [...pointers.current.values()]
    const dist = pts.length === 2 ? Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y) : 0
    gesture.current = { x: e.clientX, y: e.clientY, origin: transformRef.current, dist, moved: false }
    suppressClick.current = false
  }, [])

  const onPointerMove = useCallback(
    (e: React.PointerEvent) => {
      const g = gesture.current
      if (!g || !pointers.current.has(e.pointerId)) return
      pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
      const viewport = viewportRef.current
      if (!viewport) return
      const pts = [...pointers.current.values()]
      if (pts.length === 2 && g.dist > 0) {
        // Two-finger pinch: zoom around the midpoint.
        const rect = viewport.getBoundingClientRect()
        const dist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y)
        zoomAt(dist / g.dist, (pts[0].x + pts[1].x) / 2 - rect.left, (pts[0].y + pts[1].y) / 2 - rect.top)
        g.dist = dist
        g.moved = true
        suppressClick.current = true
        return
      }
      const dx = e.clientX - g.x
      const dy = e.clientY - g.y
      if (!g.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return // still a click on a node
      if (!g.moved) viewport.setPointerCapture?.(e.pointerId)
      g.moved = true
      suppressClick.current = true
      userMoved.current = true
      setTransform({ ...g.origin, x: g.origin.x + dx, y: g.origin.y + dy })
    },
    [setTransform, zoomAt],
  )

  const onPointerUp = useCallback((e: React.PointerEvent) => {
    pointers.current.delete(e.pointerId)
    if (pointers.current.size === 0) {
      gesture.current = null
      setTimeout(() => {
        suppressClick.current = false
      }, 0)
    }
  }, [])

  // Dim everything not connected to the selected node, and reduce label clutter below DECLUTTER_SCALE.
  // Visual-only DOM styling: never touches the generated diagram's nodes, edges or labels.
  useEffect(() => {
    const container = containerRef.current
    if (!container || !fitted) return
    const declutter = transform.scale < DECLUTTER_SCALE
    for (const label of container.querySelectorAll<HTMLElement>('.edgeLabel, .edgeLabels')) {
      label.style.opacity = declutter ? '0' : ''
      label.style.transition = 'opacity 120ms ease-out'
    }
    const nodes = container.querySelectorAll<SVGGElement>('.node')
    const edgePaths = container.querySelectorAll<SVGGElement>('.edgePaths > .edgePath, .edgePath')
    const idFromNode = (el: Element) => /^flowchart-(.+)-\d+$/.exec(el.id)?.[1]
    for (const node of nodes) {
      const id = idFromNode(node)
      const dim = !!focused && !!id && !focused.nodeIds.has(id)
      node.style.opacity = dim ? '0.25' : ''
      node.style.transition = 'opacity 120ms ease-out'
    }
    // Edge DOM ids are not a stable cross-version contract; only style edges by render order when the
    // matched element count agrees with the relationship count, otherwise leave them alone (node dimming
    // still conveys focus on its own).
    if (focused && edgePaths.length === lr.edgeEndpoints.length) {
      edgePaths.forEach((path, i) => {
        const dim = !focused.edgeIndexes.has(i)
        path.style.opacity = dim ? '0.15' : ''
        path.style.transition = 'opacity 120ms ease-out'
      })
    } else if (!focused) {
      edgePaths.forEach((path) => {
        path.style.opacity = ''
      })
    }
  }, [focused, transform.scale, fitted, lr.edgeEndpoints.length])

  const navigateTo = useCallback(
    (next: { x: number; y: number }) => {
      userMoved.current = true
      setTransform({ ...transformRef.current, x: next.x, y: next.y })
    },
    [setTransform],
  )

  const active = status.status === 'queued' || status.status === 'running'
  const chip =
    active
      ? { icon: Loader2, spin: true, text: 'Regenerating…', tone: 'text-primary bg-primary/10' }
      : status.status === 'failed'
        ? { icon: AlertCircle, spin: false, text: 'Last generation failed', tone: 'text-destructive bg-destructive/10' }
        : status.freshness === 'current'
          ? { icon: CheckCircle2, spin: false, text: 'Up to date', tone: 'text-success bg-success/10' }
          : { icon: Clock, spin: false, text: 'Out of date', tone: 'text-warning bg-warning/10' }
  const ChipIcon = chip.icon

  return (
    <div className={cn('flex h-full min-h-0 flex-col', focusMode && 'fixed inset-0 z-50 bg-background')}>
      <div className="flex min-h-9 shrink-0 flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-1.5 text-xs sm:px-5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <span role="status" className={cn('inline-flex h-6 items-center gap-1.5 rounded-md px-2 font-medium', chip.tone)}>
            <ChipIcon className={cn('size-3.5', chip.spin && 'animate-spin')} aria-hidden="true" />
            {chip.text}
          </span>
          <span className="text-muted-foreground">
            Showing commit <code className="font-mono text-foreground">{shortSha(status.generated_commit_sha)}</code>
            {status.generated_at && <> · generated {formatRelativeTime(status.generated_at)}</>}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {onRegenerate && (
            <Button size="xs" variant="outline" onClick={onRegenerate} disabled={active || regenerating}>
              <RefreshCw className={cn(regenerating && 'animate-spin')} aria-hidden="true" />
              Regenerate
            </Button>
          )}
          <Button
            size="icon-xs"
            variant={showMinimap ? 'default' : 'outline'}
            aria-label={showMinimap ? 'Hide minimap' : 'Show minimap'}
            aria-pressed={showMinimap}
            onClick={() => setShowMinimap((v) => !v)}
          >
            <MapIcon aria-hidden="true" />
          </Button>
          <Button size="icon-xs" variant="outline" aria-label="Zoom out" onClick={() => zoomButton(1 / ZOOM_STEP)}>
            <ZoomOut aria-hidden="true" />
          </Button>
          <Button size="icon-xs" variant="outline" aria-label="Zoom in" onClick={() => zoomButton(ZOOM_STEP)}>
            <ZoomIn aria-hidden="true" />
          </Button>
          <span className="min-w-9 text-center text-[11px] tabular-nums text-muted-foreground" aria-label="Zoom level">
            {Math.round(transform.scale * 100)}%
          </span>
          <Button size="xs" variant="outline" onClick={() => fitToView(true)}>
            <Maximize aria-hidden="true" />
            Reset view
          </Button>
          <Button
            size="icon-xs"
            variant="outline"
            aria-label={focusMode ? 'Exit full-screen' : 'Full screen'}
            aria-pressed={focusMode}
            onClick={() => setFocusMode((v) => !v)}
          >
            {focusMode ? <Minimize2 aria-hidden="true" /> : <Expand aria-hidden="true" />}
          </Button>
        </div>
      </div>
      <DiagramBanners status={status} requestError={requestError} />
      <div className="flex min-h-0 flex-1">
        <div
          ref={viewportRef}
          className="relative min-h-0 w-full flex-1 cursor-grab touch-none select-none overflow-hidden overscroll-contain bg-background active:cursor-grabbing"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          role="img"
          aria-label="AI-identified architecture diagram"
        >
          {renderError ? (
            <CenteredMessage icon={<TriangleAlert className="size-5 text-warning" aria-hidden="true" />} message={`Could not render the diagram: ${renderError}`} />
          ) : (
            <div
              ref={containerRef}
              className={cn('absolute left-0 top-0 [&_svg]:block', animate && 'transition-transform duration-150 ease-out motion-reduce:transition-none')}
              style={{
                transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})`,
                transformOrigin: '0 0',
                visibility: fitted ? 'visible' : 'hidden', // no jump: shown only once placed
              }}
            />
          )}
          {selected && (
            <p className="pointer-events-none absolute left-1/2 top-3 -translate-x-1/2 rounded-md border border-border bg-card/90 px-2.5 py-1 text-[11px] text-muted-foreground backdrop-blur">
              Highlighting what connects to <span className="font-medium text-foreground">{selected.label}</span> — click it again to clear
            </p>
          )}
          <Legend theme={theme} />
          {showMinimap && minimapSvg && contentBox && (
            <Minimap
              ref={minimapRef}
              svg={minimapSvg}
              content={contentBox}
              transform={transform}
              viewportSize={{ w: viewportSize.w || 1, h: viewportSize.h || 1 }}
              onNavigate={navigateTo}
            />
          )}
        </div>
        {selected && <ComponentPanel repoId={repo.id} component={selected} onClose={() => setSelectedId(null)} />}
      </div>
      {diagram.warnings.length > 0 && (
        <details className="shrink-0 border-t border-border px-3 py-1.5 text-xs text-muted-foreground sm:px-5">
          <summary className="cursor-pointer select-none">{diagram.warnings.length} generation warning{diagram.warnings.length === 1 ? '' : 's'}</summary>
          <ul className="mt-1 list-inside list-disc">
            {diagram.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}

/** Small always-to-scale overview of the whole graph, with a draggable rectangle for the main viewport's
 * current position. Click or drag inside it to jump the main view there; the zoom level is unchanged. A
 * non-interactive clone of the already-rendered (and already label-escaped) SVG — see the render effect's
 * comment on why reusing that markup here is exactly as safe as the main canvas. */
function Minimap({
  svg, content, transform, viewportSize, onNavigate, ref,
}: {
  svg: string
  content: ContentBox
  transform: Transform
  viewportSize: { w: number; h: number }
  onNavigate: (next: { x: number; y: number }) => void
  ref: React.Ref<HTMLDivElement>
}) {
  const svgHostRef = useRef<HTMLDivElement>(null)
  const dragging = useRef(false)

  useEffect(() => {
    if (svgHostRef.current) svgHostRef.current.innerHTML = svg
  }, [svg])

  const rect = viewportRectInMinimap(transform, viewportSize, { w: content.w, h: content.h }, { x: content.x, y: content.y }, MINIMAP_SIZE)

  const jump = useCallback(
    (clientX: number, clientY: number, container: HTMLDivElement) => {
      const bounds = container.getBoundingClientRect()
      const point = { x: clientX - bounds.left, y: clientY - bounds.top }
      onNavigate(
        minimapPointToTransform(point, viewportSize, { w: content.w, h: content.h }, { x: content.x, y: content.y }, MINIMAP_SIZE, transform.scale),
      )
    },
    [content, onNavigate, transform.scale, viewportSize],
  )

  return (
    <div
      ref={ref}
      className="absolute bottom-3 right-3 overflow-hidden rounded-md border border-border bg-card/90 shadow-sm backdrop-blur"
      style={{ width: MINIMAP_SIZE.w, height: MINIMAP_SIZE.h }}
      aria-label="Diagram overview (minimap)"
      onPointerDown={(e) => {
        e.stopPropagation()
        dragging.current = true
        jump(e.clientX, e.clientY, e.currentTarget)
        e.currentTarget.setPointerCapture?.(e.pointerId)
      }}
      onPointerMove={(e) => {
        if (dragging.current) jump(e.clientX, e.clientY, e.currentTarget)
      }}
      onPointerUp={() => {
        dragging.current = false
      }}
      onPointerCancel={() => {
        dragging.current = false
      }}
    >
      <div ref={svgHostRef} className="pointer-events-none absolute inset-0 opacity-80 [&_svg]:h-full [&_svg]:w-full" />
      <div
        className="absolute cursor-move border-2 border-primary bg-primary/10"
        style={{ left: rect.x, top: rect.y, width: Math.max(rect.width, 4), height: Math.max(rect.height, 4) }}
      />
    </div>
  )
}

function ComponentPanel({ repoId, component, onClose }: { repoId: string; component: DiagramComponent; onClose: () => void }) {
  return (
    <aside className="w-80 shrink-0 overflow-y-auto border-l border-border bg-sidebar/50 p-4 text-sm" aria-label="Component details">
      <div className="flex items-start justify-between gap-2">
        <h3 className="font-medium">{component.label}</h3>
        <Button size="icon-xs" variant="ghost" aria-label="Close" onClick={onClose}>
          <X aria-hidden="true" />
        </Button>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{component.kind.replace('_', ' ')}</p>
      {component.description && <p className="mt-3 text-muted-foreground">{component.description}</p>}
      <div className="mt-4">
        <p className="text-xs font-medium text-muted-foreground">
          {component.evidence_basis === 'verified_paths' ? 'Real source files' : 'No verified source file for this component'}
        </p>
        {component.source_paths.length > 0 ? (
          <ul className="mt-1.5 flex flex-col gap-1">
            {component.source_paths.map((path) => (
              <li key={path}>
                <Link href={fileLink(repoId, path)} className="inline-flex items-center gap-1 font-mono text-xs text-primary hover:underline">
                  <ExternalLink className="size-3 shrink-0" aria-hidden="true" />
                  <span className="truncate" title={path}>{path}</span>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-xs text-muted-foreground">
            The AI proposed this as a conceptual grouping with no single file backing it.
          </p>
        )}
      </div>
    </aside>
  )
}

function Legend({ theme }: { theme: 'light' | 'dark' }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="absolute bottom-3 left-3 max-w-[calc(100%-1.5rem)] rounded-md border border-border bg-card/90 text-[11px] text-muted-foreground backdrop-blur" onPointerDown={(e) => e.stopPropagation()}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls="diagram-legend"
        className="flex h-7 w-full items-center gap-1.5 rounded-md px-2.5 hover:text-foreground focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/40"
      >
        <ChevronDown className={cn('size-3 transition-transform', open && 'rotate-180')} aria-hidden="true" />
        Legend
      </button>
      {open && (
        <div id="diagram-legend" className="flex flex-col gap-1.5 px-2.5 pb-2" aria-label="Legend">
          {legendEntries(theme).map((entry) => (
            <div key={entry.category} className="flex items-center gap-2">
              <span className="size-2.5 rounded-sm border" style={{ backgroundColor: entry.fill, borderColor: entry.stroke }} aria-hidden="true" />
              {entry.label}
            </div>
          ))}
          <div className="mt-0.5 flex items-center gap-2 border-t border-border pt-1.5">
            <span className="h-px w-5 bg-foreground" aria-hidden="true" /> Verified import
          </div>
          <div className="flex items-center gap-2">
            <span className="w-5 border-t border-dashed border-muted-foreground" aria-hidden="true" /> AI-inferred relationship
          </div>
        </div>
      )}
    </div>
  )
}
