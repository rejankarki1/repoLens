import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { AnalysisStatus, Evidence, Graph, GraphEdge, GraphNode, NodeDetail } from '../api/architecture.ts'
import {
  autoRequestKey, codeLink, isImportEvidence, kindInfo, moduleDetailGraph, moduleRelations, neighbourhood, overviewGraph,
  shouldAutoRequest,
} from './model.ts'

const status = (over: Partial<AnalysisStatus>): AnalysisStatus => ({
  repository_id: 3, status: 'none', freshness: 'none', current_commit_sha: 'a'.repeat(40), analyzed_commit_sha: null,
  target_commit_sha: null, analyzed_at: null, requested_at: null, error: null, has_result: false, ...over,
})

test('an analysis is requested automatically only when there is no current result and nothing is in flight', () => {
  assert.equal(shouldAutoRequest(status({})), true)
  assert.equal(shouldAutoRequest(status({ status: 'completed', freshness: 'stale', has_result: true })), true)
  assert.equal(shouldAutoRequest(status({ status: 'completed', freshness: 'current', has_result: true })), false)
  assert.equal(shouldAutoRequest(status({ status: 'queued' })), false)
  assert.equal(shouldAutoRequest(status({ status: 'running', freshness: 'stale', has_result: true })), false)
  assert.equal(shouldAutoRequest(status({ status: 'failed', freshness: 'stale', has_result: true })), false) // retried by the user
  assert.equal(shouldAutoRequest(status({ current_commit_sha: null })), false)
  assert.equal(autoRequestKey(status({})), `3:${'a'.repeat(40)}`)
})

const ev = (over: Partial<Evidence> = {}): Evidence => ({ path: 'src/a b.ts', start_line: 4, end_line: 6, kind: 'import', detail: 'import x', ...over })

test('code links reuse the Code Explorer deep link and never invent lines', () => {
  assert.equal(codeLink('3', ev()), '/repos/3?tab=code&file=src%2Fa%20b.ts&lines=4-6')
  assert.equal(codeLink('3', ev({ start_line: 9, end_line: 9 })), '/repos/3?tab=code&file=src%2Fa%20b.ts&lines=9')
  assert.equal(codeLink('3', ev({ kind: 'path', start_line: 1, end_line: 1 })), '/repos/3?tab=code&file=src%2Fa%20b.ts')
})

test('only import statements count as verified dependency evidence', () => {
  for (const kind of ['import', 'reexport', 'require', 'lazy_import']) assert.equal(isImportEvidence({ kind }), true)
  for (const kind of ['code', 'manifest', 'path']) assert.equal(isImportEvidence({ kind }), false)
})

test('kinds map to a tone; unclassified kinds get no badge', () => {
  assert.deepEqual(kindInfo('route'), { tone: 'api', label: 'Route' })
  assert.deepEqual(kindInfo('serverless'), { tone: 'serverless', label: 'Serverless' })
  assert.deepEqual(kindInfo('directory'), { tone: 'plain', label: null })
  assert.deepEqual(kindInfo('source'), { tone: 'plain', label: null })
})

function node(id: string, over: Partial<GraphNode> = {}): GraphNode {
  const isModule = id.startsWith('module:')
  const path = id.replace(/^(module|file):/, '')
  return {
    id, label: path.split('/').pop()!, type: isModule ? 'module' : 'file', kind: isModule ? 'directory' : 'source', path, language: null,
    files: isModule ? [] : [path], file_count: 1, files_truncated: false, roles: [], area_ids: [], evidence: [],
    metrics: { incoming: 0, outgoing: 0, external_packages: 0, unresolved_imports: 0 }, cycle_id: null, ...over,
  }
}

function edge(source: string, target: string, over: Partial<GraphEdge> = {}): GraphEdge {
  const file = source.startsWith('file:')
  return {
    id: `edge:${source}->${target}`, source, target, level: file ? 'file' : 'module', relationship: file ? 'imports' : 'depends_on',
    basis: file ? 'static' : 'aggregated', resolution: 'relative', confidence: 'verified', count: 1, breakdown: {},
    evidence: [ev({ path: source.replace('file:', '') })], evidence_truncated: false, cycle_id: null, ...over,
  }
}

const graph = (over: Partial<Graph>): Graph => ({
  commit_sha: 'a'.repeat(40), analyzer_version: 1, nodes: [], edges: [], areas: [], technologies: [], external_dependencies: [],
  cycles: [], stats: {} as Graph['stats'], warnings: [], ...over,
})

test('the overview graph is exactly the backend modules and module edges', () => {
  const g = graph({
    nodes: [node('module:app', { label: 'app' }), node('module:', { label: '(root)' })],
    edges: [edge('module:app', 'module:lib', { count: 5, cycle_id: 'cycle:module:1' })],
  })
  const viz = overviewGraph(g)
  assert.deepEqual(viz.nodes.map((n) => n.label), ['app', 'repository root'])
  assert.equal(viz.edges.length, 1)
  assert.deepEqual([viz.edges[0].count, viz.edges[0].inCycle, viz.edges[0].edges[0]], [5, true, g.edges[0]])
})

test('module relations list the strongest dependencies first', () => {
  const g = graph({
    edges: [edge('module:a', 'module:b', { count: 2 }), edge('module:a', 'module:c', { count: 9 }), edge('module:d', 'module:a', { count: 1 })],
  })
  const relations = moduleRelations(g, 'module:a')
  assert.deepEqual(relations.out.map((e) => e.target), ['module:c', 'module:b'])
  assert.deepEqual(relations.in.map((e) => e.source), ['module:d'])
})

function detailFixture() {
  const members = ['file:m/a.ts', 'file:m/b.ts', 'file:m/lonely.ts'].map((id) => node(id))
  const related = [node('file:lib/x.ts'), node('file:lib/y.ts'), node('file:app/page.ts'), node('file:stray/z.ts')]
  const detail: NodeDetail = {
    analysis: status({}), node: node('module:m', { label: 'm' }), members,
    internal_edges: [edge('file:m/a.ts', 'file:m/b.ts')],
    outgoing: [edge('file:m/a.ts', 'file:lib/x.ts', { count: 2 }), edge('file:m/b.ts', 'file:lib/y.ts'), edge('file:m/a.ts', 'file:stray/z.ts')],
    incoming: [edge('file:app/page.ts', 'file:m/a.ts', { cycle_id: 'cycle:file:1' })],
    related_nodes: related, external_dependencies: [], unresolved_imports: [], cycles: [],
  }
  const g = graph({
    nodes: [
      node('module:m', { label: 'm', files: ['m/a.ts', 'm/b.ts', 'm/lonely.ts'] }),
      node('module:lib', { label: 'lib', files: ['lib/x.ts', 'lib/y.ts'], kind: 'api' }),
      node('module:app', { label: 'app', files: ['app/page.ts'] }),
    ],
  })
  return { detail, g }
}

test('module detail draws member files, merges links to other modules and sets unconnected files aside', () => {
  const { detail, g } = detailFixture()
  const viz = moduleDetailGraph(detail, g)
  assert.deepEqual(viz.hidden.map((n) => n.id), ['file:m/lonely.ts'])
  const byId = new Map(viz.nodes.map((n) => [n.id, n]))
  assert.deepEqual([...byId.keys()].sort(), ['file:m/a.ts', 'file:m/b.ts', 'neighbor:dir:stray', 'neighbor:module:app', 'neighbor:module:lib'])
  assert.equal(byId.get('neighbor:module:lib')!.kind, 'api')
  assert.equal(byId.get('neighbor:module:lib')!.moduleId, 'module:lib')
  assert.equal(byId.get('neighbor:dir:stray')!.moduleId, null) // not an overview module: never invent one

  const lib = viz.edges.find((e) => e.id === 'link:file:m/a.ts->neighbor:module:lib')!
  assert.equal(lib.count, 2)
  assert.equal(viz.edges.find((e) => e.id === 'link:file:m/b.ts->neighbor:module:lib')!.count, 1)
  assert.equal(lib.edges.length, 1)
  const incoming = viz.edges.find((e) => e.id === 'link:neighbor:module:app->file:m/a.ts')!
  assert.equal(incoming.inCycle, true)
  assert.equal(viz.edges.length, 1 + 4) // one internal edge + a->lib, b->lib, a->stray, app->a
})

test('every drawn edge carries the backend edge and its evidence', () => {
  const { detail, g } = detailFixture()
  for (const e of moduleDetailGraph(detail, g).edges) {
    assert.ok(e.edges.length >= 1 && e.edges.every((b) => b.evidence.length >= 1 && b.basis === 'static'))
  }
})

test('selecting a node emphasizes only its own edges and neighbours', () => {
  const { detail, g } = detailFixture()
  const viz = moduleDetailGraph(detail, g)
  const near = neighbourhood(viz, 'file:m/b.ts')
  assert.deepEqual([...near.nodes].sort(), ['file:m/a.ts', 'file:m/b.ts', 'neighbor:module:lib'])
  assert.equal(near.edges.size, 2)
})

test('labels resolve to real module names and paths, never invented ones', async () => {
  const { makeLabeler, isInstallable } = await import('./model.ts')
  const g = graph({ nodes: [node('module:app', { label: 'app' }), node('module:', { label: '(root)' })] })
  const label = makeLabeler(g)
  assert.deepEqual(
    ['module:app', 'module:', 'neighbor:module:app', 'neighbor:dir:stray/dir', 'file:src/a.ts', 'module:gone'].map(label),
    ['app', 'repository root', 'app', 'stray/dir', 'src/a.ts', 'gone'],
  )
  assert.deepEqual([isInstallable('npm'), isInstallable('python'), isInstallable('python_stdlib'), isInstallable('node_builtin')], [true, true, false, false])
})

test('dense graphs start with the optional layers hidden; small ones show everything', async () => {
  const { defaultFilters, DENSE_EDGE_COUNT } = await import('./model.ts')
  const viz = (edges: number) => ({ nodes: [], edges: Array.from({ length: edges }, (_, i) => ({ id: `e${i}`, source: 'a', target: 'b', count: 1, inCycle: false, edges: [] })), hidden: [] })
  assert.deepEqual(defaultFilters(viz(DENSE_EDGE_COUNT)), { typeOnly: true, neighbours: true, query: '' })
  assert.deepEqual(defaultFilters(viz(DENSE_EDGE_COUNT + 1)), { typeOnly: false, neighbours: false, query: '' })
})

test('filters hide type-only arrows and neighbour modules, and report how many arrows went', async () => {
  const { applyFilters } = await import('./model.ts')
  const { detail, g } = detailFixture()
  const typed = { ...detail, internal_edges: [edge('file:m/a.ts', 'file:m/b.ts', { relationship: 'type_only' })] }
  const viz = moduleDetailGraph(typed, g)
  const all = applyFilters(viz, { typeOnly: true, neighbours: true, query: '' })
  assert.equal(all.hiddenEdges, 0)
  assert.equal(all.edges.length, viz.edges.length)

  const noTypes = applyFilters(viz, { typeOnly: false, neighbours: true, query: '' })
  assert.equal(noTypes.hiddenEdges, 1)
  assert.ok(!noTypes.edges.some((e) => e.id === typed.internal_edges[0].id))

  const noNeighbours = applyFilters(viz, { typeOnly: true, neighbours: false, query: '' })
  assert.ok(noNeighbours.nodes.every((n) => n.type !== 'neighbor'))
  assert.ok(noNeighbours.edges.every((e) => !e.source.startsWith('neighbor:') && !e.target.startsWith('neighbor:')))
  assert.equal(noNeighbours.hiddenEdges, viz.edges.length - 1)
})

test('a module-level arrow is type-only only when all of its imports are', async () => {
  const { applyFilters, overviewGraph } = await import('./model.ts')
  const g = graph({
    nodes: [node('module:a', { label: 'a' }), node('module:b', { label: 'b' }), node('module:c', { label: 'c' })],
    edges: [edge('module:a', 'module:b', { breakdown: { type_only: 3 } }), edge('module:a', 'module:c', { breakdown: { type_only: 1, imports: 2 } })],
  })
  const out = applyFilters(overviewGraph(g), { typeOnly: false, neighbours: true, query: '' })
  assert.deepEqual(out.edges.map((e) => e.target), ['module:c'])
})

test('search matches names and paths case-insensitively and an empty query matches nothing', async () => {
  const { matchingNodes, overviewGraph } = await import('./model.ts')
  const viz = overviewGraph(graph({ nodes: [node('module:src/Services', { label: 'src/Services' }), node('module:src/lib', { label: 'src/lib' })] }))
  assert.deepEqual([...matchingNodes(viz, 'SERV')], ['module:src/Services'])
  assert.deepEqual([...matchingNodes(viz, 'src')].length, 2)
  assert.equal(matchingNodes(viz, '  ').size, 0)
})

test('only the languages the analysis reads are looked up', async () => {
  const { isAnalyzedPath } = await import('./model.ts')
  for (const yes of ['a.py', 'a.pyi', 'src/x.ts', 'x.tsx', 'a.js', 'a.jsx', 'a.mjs', 'a.cjs', 'a.mts', 'a.cts', 'a.d.ts']) assert.equal(isAnalyzedPath(yes), true, yes)
  for (const no of ['README.md', 'package.json', 'a.css', 'Makefile', 'a.pyc', 'x.json', 'a.tsx.map']) assert.equal(isAnalyzedPath(no), false, no)
})
