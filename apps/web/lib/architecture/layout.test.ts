import assert from 'node:assert/strict'
import { test } from 'node:test'
import { layoutGraph, type LayoutEdge, type LayoutNode } from './layout.ts'

const node = (id: string, width = 200, height = 70): LayoutNode => ({ id, width, height })
const nodesOf = (...ids: string[]) => ids.map((id) => node(id))

function overlaps(result: ReturnType<typeof layoutGraph>, nodes: LayoutNode[]) {
  const boxes = nodes.map((n) => ({ id: n.id, ...result.positions.get(n.id)!, w: n.width, h: n.height }))
  const clashes: string[] = []
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i]
      const b = boxes[j]
      if (a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h) clashes.push(`${a.id}/${b.id}`)
    }
  }
  return clashes
}

test('dependants sit to the left of what they depend on', () => {
  const nodes = nodesOf('app', 'services', 'lib')
  const result = layoutGraph(nodes, [
    { source: 'app', target: 'services' },
    { source: 'services', target: 'lib' },
    { source: 'app', target: 'lib' },
  ])
  const x = (id: string) => result.positions.get(id)!.x
  assert.ok(x('app') < x('services') && x('services') < x('lib'))
})

test('a source with a single dependency is placed next to it, not at the far left', () => {
  const nodes = nodesOf('a', 'b', 'c', 'leaf')
  const result = layoutGraph(nodes, [
    { source: 'a', target: 'b' },
    { source: 'b', target: 'c' },
    { source: 'leaf', target: 'c' },
  ])
  assert.equal(result.positions.get('leaf')!.x, result.positions.get('b')!.x)
})

test('cycles do not break the layout and every node is placed', () => {
  const nodes = nodesOf('a', 'b', 'c', 'd')
  const edges: LayoutEdge[] = [
    { source: 'a', target: 'b' },
    { source: 'b', target: 'c' },
    { source: 'c', target: 'a' },
    { source: 'c', target: 'd' },
    { source: 'd', target: 'd' },
  ]
  const result = layoutGraph(nodes, edges)
  assert.equal(result.positions.size, 4)
  assert.deepEqual(overlaps(result, nodes), [])
})

test('isolated nodes go in a row under the graph instead of widening it', () => {
  const nodes = nodesOf('a', 'b', 'docs', 'scripts')
  const result = layoutGraph(nodes, [{ source: 'a', target: 'b' }])
  const at = (id: string) => result.positions.get(id)!
  assert.ok(at('docs').y > at('a').y + 70 && at('docs').y === at('scripts').y)
  assert.ok(at('scripts').x > at('docs').x)
  assert.deepEqual(overlaps(result, nodes), [])
})

test('a graph with no edges and an empty graph both work', () => {
  const nodes = nodesOf('a', 'b', 'c')
  const result = layoutGraph(nodes, [])
  assert.equal(result.positions.size, 3)
  assert.deepEqual(overlaps(result, nodes), [])
  const empty = layoutGraph([], [])
  assert.deepEqual([empty.positions.size, empty.width, empty.height], [0, 0, 0])
})

test('edges to unknown nodes and duplicate edges are ignored', () => {
  const nodes = nodesOf('a', 'b')
  const result = layoutGraph(nodes, [
    { source: 'a', target: 'b' },
    { source: 'a', target: 'b' },
    { source: 'a', target: 'ghost' },
  ])
  assert.equal(result.positions.size, 2)
})

test('tall layers wrap into several columns', () => {
  const nodes = [node('root'), ...Array.from({ length: 25 }, (_, i) => node(`m${String(i).padStart(2, '0')}`))]
  const edges = nodes.slice(1).map((n) => ({ source: 'root', target: n.id }))
  const result = layoutGraph(nodes, edges, { maxPerColumn: 8 })
  const columns = new Set(nodes.slice(1).map((n) => result.positions.get(n.id)!.x))
  assert.equal(columns.size, 4)
  assert.deepEqual(overlaps(result, nodes), [])
})

test('layout is deterministic and never overlaps on a random graph', () => {
  let seed = 7
  const random = () => (seed = (seed * 1664525 + 1013904223) % 2 ** 32) / 2 ** 32
  const nodes = Array.from({ length: 60 }, (_, i) => node(`n${i}`, 160 + (i % 3) * 30, 56 + (i % 2) * 20))
  const edges = Array.from({ length: 140 }, () => ({
    source: `n${Math.floor(random() * 60)}`,
    target: `n${Math.floor(random() * 60)}`,
  }))
  const first = layoutGraph(nodes, edges)
  const second = layoutGraph([...nodes].reverse(), [...edges].reverse())
  assert.deepEqual(overlaps(first, nodes), [])
  for (const n of nodes) assert.deepEqual(first.positions.get(n.id), second.positions.get(n.id))
})
