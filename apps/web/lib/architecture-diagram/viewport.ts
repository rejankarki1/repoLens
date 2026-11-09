/**
 * Pure geometry and graph helpers for the architecture diagram canvas (`architecture/diagram-view.tsx`).
 * Nothing here touches the DOM or the generated diagram's content (nodes/edges/labels are never changed
 * here) - this is strictly the math behind fitting, zoom bounds, the minimap, and "what is connected to
 * the selected node", kept separate so it is unit-testable without a browser.
 */

export interface Size {
  w: number
  h: number
}

export interface Rect {
  x: number
  y: number
  width: number
  height: number
}

export interface Transform {
  x: number
  y: number
  scale: number
}

/** Natural size of a Mermaid SVG string, from its viewBox (null if it has none). */
export function svgSize(svg: string): Size | null {
  const m = /viewBox="[-\d.]+ [-\d.]+ ([\d.]+) ([\d.]+)"/.exec(svg)
  return m ? { w: Number(m[1]), h: Number(m[2]) } : null
}

/** The scale at which content of `size` fits entirely inside a `vw`x`vh` viewport, minus `padding` on
 * every side. The content's own aspect ratio is always preserved: the viewer never stretches a wide
 * graph into a fixed frame, it fits whatever shape the graph actually is. */
export function fitScaleFor(size: Size, vw: number, vh: number, padding = 0): number {
  if (size.w <= 0 || size.h <= 0 || vw <= 0 || vh <= 0) return 1
  return Math.min((vw - padding * 2) / size.w, (vh - padding * 2) / size.h)
}

/** The scale `fitToView` should use: fits the content when that keeps it legible, otherwise holds at
 * `readableScale` and lets the user pan the overflowing axis (a huge graph never shrinks to unreadable
 * text), capped at `maxScale` (a tiny graph never blows up past a sane size). */
export function clampedFitScale(size: Size, vw: number, vh: number, opts: { readableScale: number; maxScale: number; padding: number }): number {
  return Math.min(opts.maxScale, Math.max(opts.readableScale, fitScaleFor(size, vw, vh, opts.padding)))
}

/** The lowest scale the user should be able to zoom out to: low enough to see the *entire* graph at
 * once even when it is much bigger than the viewport, but never below `floor` (so tiny graphs do not
 * zoom out to a speck). Unlike `clampedFitScale`, this intentionally ignores the readable-text floor -
 * it is a user-chosen "show me everything" zoom level, not the automatic initial fit. */
export function adaptiveMinScale(size: Size, vw: number, vh: number, floor: number): number {
  return Math.min(floor, fitScaleFor(size, vw, vh))
}

/** Where `content` (in its own coordinate space) sits within the minimap box, preserving aspect ratio
 * and centered - mirrors how the real canvas never stretches the graph to a fixed frame. */
export function minimapPlacement(content: Size, minimap: Size): { scale: number; offsetX: number; offsetY: number } {
  const scale = fitScaleFor(content, minimap.w, minimap.h)
  return { scale, offsetX: (minimap.w - content.w * scale) / 2, offsetY: (minimap.h - content.h * scale) / 2 }
}

/** The main viewport's visible region, in content coordinates, given the current pan/zoom `transform`. */
export function visibleContentRect(transform: Transform, viewport: Size): Rect {
  return {
    x: -transform.x / transform.scale,
    y: -transform.y / transform.scale,
    width: viewport.w / transform.scale,
    height: viewport.h / transform.scale,
  }
}

/** The viewport-rectangle overlay to draw inside the minimap, in minimap-local pixels. `contentOrigin` is
 * the content box's own (x, y) origin (a Mermaid viewBox is not necessarily zero-based). */
export function viewportRectInMinimap(
  transform: Transform,
  viewport: Size,
  content: Size,
  contentOrigin: { x: number; y: number },
  minimap: Size,
): Rect {
  const { scale, offsetX, offsetY } = minimapPlacement(content, minimap)
  const visible = visibleContentRect(transform, viewport)
  return {
    x: offsetX + (visible.x - contentOrigin.x) * scale,
    y: offsetY + (visible.y - contentOrigin.y) * scale,
    width: visible.width * scale,
    height: visible.height * scale,
  }
}

/** Inverse of the minimap placement: a click/drag at minimap-local pixel `point` becomes the new
 * viewport *center*, at the given `scale` (the user's current zoom level is preserved; only pan moves). */
export function minimapPointToTransform(
  point: { x: number; y: number },
  viewport: Size,
  content: Size,
  contentOrigin: { x: number; y: number },
  minimap: Size,
  scale: number,
): { x: number; y: number } {
  const { scale: mapScale, offsetX, offsetY } = minimapPlacement(content, minimap)
  const cx = contentOrigin.x + (point.x - offsetX) / mapScale
  const cy = contentOrigin.y + (point.y - offsetY) / mapScale
  return { x: viewport.w / 2 - (cx - contentOrigin.x) * scale, y: viewport.h / 2 - (cy - contentOrigin.y) * scale }
}

export interface Edge {
  source: string
  target: string
}

/** Every node id directly connected to `nodeId` (itself included), and the indexes into `edges` of every
 * edge touching it - used to dim everything else when a node is focused. `edges` must be in the same
 * order `diagramToMermaid` drew them (its returned `edgeEndpoints`), since the rendered SVG's edges are
 * matched back to this array by position, not by parsing the DOM. */
export function connectedToNode(edges: Edge[], nodeId: string): { nodeIds: Set<string>; edgeIndexes: Set<number> } {
  const nodeIds = new Set<string>([nodeId])
  const edgeIndexes = new Set<number>()
  edges.forEach((e, i) => {
    if (e.source === nodeId || e.target === nodeId) {
      edgeIndexes.add(i)
      nodeIds.add(e.source)
      nodeIds.add(e.target)
    }
  })
  return { nodeIds, edgeIndexes }
}
