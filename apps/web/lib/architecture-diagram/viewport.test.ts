import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  adaptiveMinScale, clampedFitScale, connectedToNode, fitScaleFor, minimapPointToTransform, svgSize, viewportRectInMinimap,
} from './viewport.ts'

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
