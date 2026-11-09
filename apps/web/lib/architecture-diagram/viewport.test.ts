import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  adaptiveMinScale, centeredTransform, clampedFitScale, connectedToNode, fitScaleFor, minimapPointToTransform, panBy, svgSize,
  viewportRectInMinimap, wheelIntent, zoomTowardPoint,
} from './viewport.ts'

function wheelEvent(over: Partial<{ ctrlKey: boolean; metaKey: boolean; shiftKey: boolean; deltaX: number; deltaY: number; deltaMode: number }>) {
  return { ctrlKey: false, metaKey: false, shiftKey: false, deltaX: 0, deltaY: 0, deltaMode: 0, ...over }
}

test('fitScaleFor preserves the content aspect ratio and never stretches it to a fixed frame', () => {
  // A wide graph (1000x200) in a near-square viewport: the binding constraint is width.
  assert.equal(fitScaleFor({ w: 1000, h: 200 }, 500, 500), 0.5)
  // A tall graph in a wide viewport: the binding constraint is height.
  assert.equal(fitScaleFor({ w: 200, h: 1000 }, 500, 500), 0.5)
})

test('fitScaleFor subtracts padding from every side', () => {
  assert.equal(fitScaleFor({ w: 100, h: 100 }, 220, 220, 10), 2) // (220 - 2*10) / 100
})

test('fitScaleFor degenerates to 1 rather than dividing by zero or returning Infinity/NaN', () => {
  assert.equal(fitScaleFor({ w: 0, h: 100 }, 500, 500), 1)
  assert.equal(fitScaleFor({ w: 100, h: 100 }, 0, 500), 1)
})

test('clampedFitScale holds at the readable floor for a graph too large to fit legibly', () => {
  const scale = clampedFitScale({ w: 5000, h: 5000 }, 800, 600, { readableScale: 0.55, maxScale: 1.5, padding: 32 })
  assert.equal(scale, 0.55)
})

test('clampedFitScale caps at maxScale for a tiny graph so it never balloons', () => {
  const scale = clampedFitScale({ w: 50, h: 50 }, 800, 600, { readableScale: 0.55, maxScale: 1.5, padding: 32 })
  assert.equal(scale, 1.5)
})

test('clampedFitScale fits exactly when the content is between the two bounds', () => {
  const scale = clampedFitScale({ w: 800, h: 400 }, 1000, 1000, { readableScale: 0.1, maxScale: 3, padding: 0 })
  assert.equal(scale, 1.25) // 1000 / 800, the binding (width) constraint
})

test('adaptiveMinScale lets a huge graph zoom out past the usual floor to show it all', () => {
  const scale = adaptiveMinScale({ w: 10_000, h: 10_000 }, 800, 600, 0.1)
  assert.ok(scale < 0.1)
  assert.equal(scale, fitScaleFor({ w: 10_000, h: 10_000 }, 800, 600))
})

test('adaptiveMinScale never goes below the floor for a small graph', () => {
  const scale = adaptiveMinScale({ w: 100, h: 100 }, 800, 600, 0.1)
  assert.equal(scale, 0.1)
})

test('viewportRectInMinimap places the full-content rectangle exactly inside the minimap at scale 1 (no pan)', () => {
  const rect = viewportRectInMinimap(
    { x: 0, y: 0, scale: 1 },
    { w: 400, h: 400 }, // viewport same size as content: the whole thing is visible
    { w: 400, h: 400 },
    { x: 0, y: 0 },
    { w: 200, h: 200 },
  )
  assert.equal(rect.x, 0)
  assert.equal(rect.y, 0)
  assert.equal(rect.width, 200)
  assert.equal(rect.height, 200)
})

test('viewportRectInMinimap shrinks the rectangle when zoomed in (less of the content is visible)', () => {
  const rect = viewportRectInMinimap(
    { x: 0, y: 0, scale: 2 }, // zoomed in 2x: half the content fills the viewport
    { w: 400, h: 400 },
    { w: 400, h: 400 },
    { x: 0, y: 0 },
    { w: 200, h: 200 },
  )
  assert.equal(rect.width, 100)
  assert.equal(rect.height, 100)
})

test('minimapPointToTransform is the inverse of viewportRectInMinimap for the rectangle it produces', () => {
  const content = { w: 400, h: 400 }
  const viewport = { w: 400, h: 400 }
  const minimap = { w: 200, h: 200 }
  const original = { x: -50, y: -50, scale: 1.5 }
  const rect = viewportRectInMinimap(original, viewport, content, { x: 0, y: 0 }, minimap)
  const center = { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 }
  const back = minimapPointToTransform(center, viewport, content, { x: 0, y: 0 }, minimap, original.scale)
  assert.ok(Math.abs(back.x - original.x) < 1e-6)
  assert.ok(Math.abs(back.y - original.y) < 1e-6)
})

test('connectedToNode includes the node itself, its direct neighbors, and only the touching edge indexes', () => {
  const edges = [{ source: 'a', target: 'b' }, { source: 'c', target: 'd' }, { source: 'b', target: 'e' }]
  const { nodeIds, edgeIndexes } = connectedToNode(edges, 'b')
  assert.deepEqual([...nodeIds].sort(), ['a', 'b', 'e'])
  assert.deepEqual([...edgeIndexes].sort(), [0, 2])
})

test('connectedToNode on a node with no edges returns just itself', () => {
  const { nodeIds, edgeIndexes } = connectedToNode([{ source: 'a', target: 'b' }], 'z')
  assert.deepEqual([...nodeIds], ['z'])
  assert.equal(edgeIndexes.size, 0)
})

test('svgSize reads width/height from a viewBox attribute', () => {
  assert.deepEqual(svgSize('<svg viewBox="0 0 640 480">'), { w: 640, h: 480 })
})

test('svgSize returns null when there is no viewBox', () => {
  assert.equal(svgSize('<svg>'), null)
})

test('panBy moves position without touching scale', () => {
  assert.deepEqual(panBy({ x: 10, y: 20, scale: 1.5 }, 5, -3), { x: 15, y: 17, scale: 1.5 })
})

test('zoomTowardPoint keeps the content under the cursor fixed on screen', () => {
  // A point at screen (100, 100), with the canvas currently untransformed: zooming 2x around it should
  // leave that exact screen point showing the same content point afterwards.
  const before = { x: 0, y: 0, scale: 1 }
  const after = zoomTowardPoint(before, 2, { x: 100, y: 100 }, 0.1, 3)
  assert.equal(after.scale, 2)
  // content point under the cursor, in content space: (100 - before.x) / before.scale = 100
  // after zooming: screenX = after.x + 100 * after.scale must still equal 100
  assert.equal(after.x + 100 * after.scale, 100)
  assert.equal(after.y + 100 * after.scale, 100)
})

test('zoomTowardPoint clamps to the given bounds and leaves the transform untouched at the bound', () => {
  const atMax = { x: 3, y: 4, scale: 3 }
  assert.deepEqual(zoomTowardPoint(atMax, 1.5, { x: 50, y: 50 }, 0.1, 3), atMax)
  const atMin = { x: 3, y: 4, scale: 0.1 }
  assert.deepEqual(zoomTowardPoint(atMin, 1 / 1.5, { x: 50, y: 50 }, 0.1, 3), atMin)
})

test('zoomTowardPoint zooming out around the same point is the exact inverse of zooming in', () => {
  const start = { x: 7, y: -4, scale: 1 }
  const zoomedIn = zoomTowardPoint(start, 2, { x: 120, y: 80 }, 0.1, 10)
  const back = zoomTowardPoint(zoomedIn, 0.5, { x: 120, y: 80 }, 0.1, 10)
  assert.ok(Math.abs(back.x - start.x) < 1e-9)
  assert.ok(Math.abs(back.y - start.y) < 1e-9)
  assert.ok(Math.abs(back.scale - start.scale) < 1e-9)
})

test('centeredTransform centers content that fits, exactly matching the original whole-diagram fit formula', () => {
  // The whole-diagram case: rect starts exactly at the content origin.
  const t = centeredTransform({ x: 0, y: 0, width: 400, height: 200 }, { x: 0, y: 0 }, { w: 1000, h: 1000 }, 1, 32)
  // Original formula: place(content, view) = content <= view - 2*padding ? (view - content) / 2 : padding
  assert.equal(t.x, (1000 - 400) / 2)
  assert.equal(t.y, (1000 - 200) / 2)
})

test('centeredTransform pins to padding, not the center, when the content overflows the viewport', () => {
  const t = centeredTransform({ x: 0, y: 0, width: 2000, height: 2000 }, { x: 0, y: 0 }, { w: 500, h: 500 }, 1, 32)
  assert.equal(t.x, 32)
  assert.equal(t.y, 32)
})

test('centeredTransform correctly centers a sub-rect that does not start at the content origin (node focus)', () => {
  // A node's bbox sitting in the middle of a larger diagram, at content-space (450, 300), size 80x40.
  const rect = { x: 450, y: 300, width: 80, height: 40 }
  const contentOrigin = { x: 0, y: 0 }
  const viewport = { w: 800, h: 600 }
  const scale = 2
  const t = centeredTransform(rect, contentOrigin, viewport, scale, 24)
  // The rect's center, mapped through (x, y) + (point - contentOrigin) * scale, must land on the viewport center.
  const centerX = rect.x + rect.width / 2
  const centerY = rect.y + rect.height / 2
  assert.ok(Math.abs(t.x + (centerX - contentOrigin.x) * scale - viewport.w / 2) < 1e-9)
  assert.ok(Math.abs(t.y + (centerY - contentOrigin.y) * scale - viewport.h / 2) < 1e-9)
})

test('centeredTransform accounts for a non-zero content origin (a Mermaid viewBox is not always zero-based)', () => {
  const t = centeredTransform({ x: 50, y: 50, width: 100, height: 100 }, { x: 50, y: 50 }, { w: 300, h: 300 }, 1, 10)
  // Same shape and relative position as the zero-origin whole-diagram case, just offset - result must match.
  const zeroOrigin = centeredTransform({ x: 0, y: 0, width: 100, height: 100 }, { x: 0, y: 0 }, { w: 300, h: 300 }, 1, 10)
  assert.deepEqual(t, zeroOrigin)
})

// --- wheelIntent: the actual bug (every wheel event used to zoom) --------------------------------------

test('an ordinary mouse wheel (deltaY only, no modifier keys) pans vertically, not zoom', () => {
  const intent = wheelIntent(wheelEvent({ deltaY: 100 }))
  assert.deepEqual(intent, { type: 'pan', dx: 0, dy: 100 })
})

test('a two-finger trackpad swipe (deltaX and deltaY, no modifier keys) pans in the swiped direction', () => {
  const intent = wheelIntent(wheelEvent({ deltaX: 30, deltaY: -50 }))
  assert.deepEqual(intent, { type: 'pan', dx: 30, dy: -50 })
})

test('ctrlKey (a trackpad pinch, or a literal Ctrl+wheel) zooms, never pans', () => {
  const intent = wheelIntent(wheelEvent({ ctrlKey: true, deltaY: 40, deltaX: 10 }))
  assert.equal(intent.type, 'zoom')
  assert.equal((intent as { delta: number }).delta, 40) // deltaX is irrelevant to a zoom gesture
})

test('metaKey (Cmd+wheel) also zooms - the browser does not set ctrlKey for Cmd, so this must be explicit', () => {
  const intent = wheelIntent(wheelEvent({ metaKey: true, deltaY: 25 }))
  assert.equal(intent.type, 'zoom')
})

test('shiftKey forces horizontal panning using deltaX when the device already provides it', () => {
  const intent = wheelIntent(wheelEvent({ shiftKey: true, deltaX: 15, deltaY: 99 }))
  assert.deepEqual(intent, { type: 'pan', dx: 15, dy: 0 })
})

test('shiftKey falls back to deltaY as the horizontal source when the device does not remap it itself', () => {
  const intent = wheelIntent(wheelEvent({ shiftKey: true, deltaX: 0, deltaY: 60 }))
  assert.deepEqual(intent, { type: 'pan', dx: 60, dy: 0 })
})

test('wheelIntent scales by deltaMode (line and page units), for both pan and zoom', () => {
  assert.deepEqual(wheelIntent(wheelEvent({ deltaY: 2, deltaMode: 1 })), { type: 'pan', dx: 0, dy: 32 }) // lines: *16
  assert.equal((wheelIntent(wheelEvent({ ctrlKey: true, deltaY: 1, deltaMode: 2 })) as { delta: number }).delta, 400) // pages: *400
})
