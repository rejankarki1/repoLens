/**
 * Layered left-to-right layout for a dependency graph (an edge `a -> b` means "a depends on b", so dependants sit to
 * the left of what they depend on). Pure and deterministic: the same input gives the same positions.
 *
 * Steps: set nodes without any edge aside; break cycles by ignoring the edges a depth-first search finds pointing
 * back; layer the rest by longest path; order each layer by barycenter sweeps to cut crossings; wrap tall layers into
 * several columns so a wide graph does not become one very long strip.
 */

export interface LayoutNode {
  id: string
  width: number
  height: number
}

export interface LayoutEdge {
  source: string
  target: string
}

export interface Positioned {
  x: number // top-left corner
  y: number
}

export interface LayoutOptions {
  gapX?: number
  gapY?: number
  /** Most nodes stacked in one column before a layer wraps into another column. */
  maxPerColumn?: number
}

export interface LayoutResult {
  positions: Map<string, Positioned>
  width: number
  height: number
}

export function layoutGraph(nodes: LayoutNode[], edges: LayoutEdge[], options: LayoutOptions = {}): LayoutResult {
  const gapX = options.gapX ?? 64
  const gapY = options.gapY ?? 24
  const maxPerColumn = options.maxPerColumn ?? 9
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const ids = [...byId.keys()].sort()

  const out = new Map<string, string[]>(ids.map((id) => [id, []]))
  const inn = new Map<string, string[]>(ids.map((id) => [id, []]))
  const seen = new Set<string>()
  for (const e of edges) {
    const key = `${e.source} ${e.target}`
    if (e.source === e.target || !byId.has(e.source) || !byId.has(e.target) || seen.has(key)) continue
    seen.add(key)
    out.get(e.source)!.push(e.target)
    inn.get(e.target)!.push(e.source)
  }
  for (const list of [...out.values(), ...inn.values()]) list.sort()

  const connected = ids.filter((id) => out.get(id)!.length + inn.get(id)!.length > 0)
  const connectedSet = new Set(connected)
  const isolated = ids.filter((id) => !connectedSet.has(id))

  // 1. ignore back edges (iterative DFS, starting from nodes nothing depends on)
  const dag = new Map<string, string[]>(connected.map((id) => [id, []]))
  const color = new Map<string, 1 | 2>()
  const roots = [...connected].sort((a, b) => inn.get(a)!.length - inn.get(b)!.length || (a < b ? -1 : 1))
  for (const root of roots) {
    if (color.get(root)) continue
    color.set(root, 1)
    const stack: [string, number][] = [[root, 0]]
    while (stack.length) {
      const frame = stack[stack.length - 1]
      const children = out.get(frame[0])!
      if (frame[1] >= children.length) {
        color.set(frame[0], 2)
        stack.pop()
        continue
      }
      const child = children[frame[1]++]
      if (color.get(child) === 1) continue // back edge: part of a cycle, not used for layering
      dag.get(frame[0])!.push(child)
      if (!color.get(child)) {
        color.set(child, 1)
        stack.push([child, 0])
      }
    }
  }

  // 2. longest-path layering over the DAG
  const indeg = new Map<string, number>(connected.map((id) => [id, 0]))
  for (const targets of dag.values()) for (const t of targets) indeg.set(t, indeg.get(t)! + 1)
  const layer = new Map<string, number>(connected.map((id) => [id, 0]))
  const queue = connected.filter((id) => indeg.get(id) === 0)
  for (let i = 0; i < queue.length; i++) {
    const id = queue[i]
    for (const t of dag.get(id)!) {
      layer.set(t, Math.max(layer.get(t)!, layer.get(id)! + 1))
      indeg.set(t, indeg.get(t)! - 1)
      if (indeg.get(t) === 0) queue.push(t)
    }
  }
  // pull pure sources next to what they depend on instead of leaving them at layer 0
  for (const id of connected) {
    if (inn.get(id)!.length === 0 && dag.get(id)!.length > 0) {
      layer.set(id, Math.min(...dag.get(id)!.map((t) => layer.get(t)!)) - 1)
    }
  }
  const lowest = connected.length ? Math.min(...connected.map((id) => layer.get(id)!)) : 0
  const layers: string[][] = []
  for (const id of connected) {
    const l = layer.get(id)! - lowest
    if (!layers[l]) layers[l] = []
    layers[l].push(id)
  }
  for (let i = 0; i < layers.length; i++) if (!layers[i]) layers[i] = []

  // 3. order within layers by barycenter
  const order = new Map<string, number>()
  layers.forEach((l) => l.forEach((id, i) => order.set(id, i)))
  const barycenter = (id: string, neighbours: string[]) => {
    const placed = neighbours.filter((n) => order.has(n))
    return placed.length ? placed.reduce((s, n) => s + order.get(n)!, 0) / placed.length : order.get(id)!
  }
  for (let pass = 0; pass < 6; pass++) {
    const downward = pass % 2 === 0
    const sequence = downward ? [...layers.keys()] : [...layers.keys()].reverse()
    for (const l of sequence) {
      const score = new Map(layers[l].map((id) => [id, barycenter(id, downward ? inn.get(id)! : out.get(id)!)]))
      layers[l].sort((a, b) => score.get(a)! - score.get(b)! || (a < b ? -1 : 1))
      layers[l].forEach((id, i) => order.set(id, i))
    }
  }

  // 4. coordinates
  let x = 0
  let tallest = 0
  const columns: { ids: string[]; x: number; width: number }[] = []
  const place = (group: string[]) => {
    for (let start = 0; start < group.length; start += maxPerColumn) {
      const members = group.slice(start, start + maxPerColumn)
      const width = Math.max(...members.map((id) => byId.get(id)!.width))
      columns.push({ ids: members, x, width })
      x += width + gapX
      tallest = Math.max(tallest, members.reduce((s, id) => s + byId.get(id)!.height, 0) + gapY * (members.length - 1))
    }
  }
  for (const group of layers) place(group)
  const graphWidth = Math.max(0, x - gapX)

  const positions = new Map<string, Positioned>()
  for (const col of columns) {
    const height = col.ids.reduce((s, id) => s + byId.get(id)!.height, 0) + gapY * (col.ids.length - 1)
    let y = (tallest - height) / 2
    for (const id of col.ids) {
      const node = byId.get(id)!
      positions.set(id, { x: col.x + (col.width - node.width) / 2, y })
      y += node.height + gapY
    }
  }

  // nodes with no dependencies go in rows under the graph, so they do not widen it
  let bottom = tallest
  if (isolated.length) {
    const rowHeight = Math.max(...isolated.map((id) => byId.get(id)!.height))
    const cellWidth = Math.max(...isolated.map((id) => byId.get(id)!.width)) + gapX / 2
    const perRow = Math.max(2, Math.floor((Math.max(graphWidth, cellWidth * 3) + gapX / 2) / cellWidth))
    let y = tallest + (connected.length ? gapY * 2.5 : 0)
    for (let start = 0; start < isolated.length; start += perRow) {
      isolated.slice(start, start + perRow).forEach((id, i) => positions.set(id, { x: i * cellWidth, y }))
      y += rowHeight + gapY
    }
    bottom = y - gapY
    x = Math.max(x, Math.min(perRow, isolated.length) * cellWidth - gapX / 2 + gapX)
  }
  return { positions, width: Math.max(0, x - gapX), height: bottom }
}
