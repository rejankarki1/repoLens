import assert from 'node:assert/strict'
import { test } from 'node:test'
import { DIAGRAM_CLICK_CALLBACK, diagramToMermaid, escapeMermaidLabel } from './mermaid.ts'
import type { ArchitectureDiagram, DiagramComponent, DiagramRelationship } from './types.ts'

function component(over: Partial<DiagramComponent>): DiagramComponent {
  return {
    id: 'c1', group_id: null, label: 'Component', description: '', kind: 'service', source_paths: [],
    evidence_basis: 'inferred', ...over,
  }
}

function relationship(over: Partial<DiagramRelationship>): DiagramRelationship {
  return { id: 'r1', source: 'c1', target: 'c2', label: 'calls', kind: 'other', confidence: 'inferred', evidence: [], ...over }
}

function diagram(over: Partial<ArchitectureDiagram>): ArchitectureDiagram {
  return { repository_commit_sha: 'a'.repeat(40), generator_version: 1, groups: [], components: [], relationships: [], warnings: [], ...over }
}

test('is deterministic: the same diagram always produces identical mermaid text', () => {
  const d = diagram({
    groups: [{ id: 'g1', label: 'Backend', description: '' }],
    components: [component({ id: 'c1', group_id: 'g1', label: 'API' }), component({ id: 'c2', label: 'Lib' })],
    relationships: [relationship({ source: 'c1', target: 'c2' })],
  })
  assert.equal(diagramToMermaid(d).text, diagramToMermaid(d).text)
})

test('groups become subgraphs containing only their own members; ungrouped components sit outside any subgraph', () => {
  const d = diagram({
    groups: [{ id: 'g1', label: 'Backend', description: '' }],
    components: [component({ id: 'c1', group_id: 'g1' }), component({ id: 'c2', group_id: null })],
  })
  const { text } = diagramToMermaid(d)
  const lines = text.split('\n')
  const subgraphStart = lines.findIndex((l) => l.includes('subgraph'))
  const subgraphEnd = lines.findIndex((l) => l.trim() === 'end')
  const inside = lines.slice(subgraphStart, subgraphEnd).join('\n')
  assert.ok(inside.includes('c1'))
  assert.ok(!inside.includes('c2'))
  assert.ok(text.slice(0, subgraphStart).concat(text.slice(subgraphEnd)).includes('c2') || text.includes('\n  c2['))
})

test('a component referencing a group id that does not exist is treated as ungrouped, not dropped', () => {
  const d = diagram({ groups: [], components: [component({ id: 'c1', group_id: 'ghost' })] })
  const { text, nodeIdToComponentId } = diagramToMermaid(d)
  assert.ok(text.includes('c1['))
  assert.equal(nodeIdToComponentId.c1, 'c1')
})

test('node shape follows component kind: database/store cylinder, external_service hexagon, ui/page pill, worker/job/pipeline subroutine, else rectangle', () => {
  const kinds: [DiagramComponent['kind'], string][] = [
    ['database', '[('], ['store', '[('], ['external_service', '{{'], ['ui', '(['], ['page', '(['],
    ['worker', '[['], ['job', '[['], ['pipeline', '[['],
  ]
  for (const [kind, openToken] of kinds) {
    const d = diagram({ components: [component({ id: 'c1', kind })] })
    const line = diagramToMermaid(d).text.split('\n').find((l) => l.includes('c1'))!
    assert.ok(line.includes(openToken), `kind ${kind} expected to contain ${openToken!}, got: ${line}`)
  }
  for (const kind of ['service', 'api', 'library', 'other', 'cli'] as const) {
    const d = diagram({ components: [component({ id: 'c1', kind })] })
    const line = diagramToMermaid(d).text.split('\n').find((l) => l.includes('c1'))!
    assert.ok(line.includes('c1['), `kind ${kind} expected a plain rectangle, got: ${line}`)
    assert.ok(!line.includes('[['), `kind ${kind} must not use the worker subroutine shape, got: ${line}`)
  }
})

test('worker/job/pipeline components get their own visual category, distinct from service/api', () => {
  const d = diagram({
    components: [component({ id: 'c1', kind: 'job' }), component({ id: 'c2', kind: 'service' })],
  })
  const { text } = diagramToMermaid(d)
  assert.ok(text.includes('class c1 cat_worker'))
  assert.ok(text.includes('class c2 cat_process'))
})

test('a verified relationship is a solid arrow; an inferred one is dashed and gets a linkStyle override', () => {
  const d = diagram({
    components: [component({ id: 'c1' }), component({ id: 'c2' })],
    relationships: [
      relationship({ id: 'r1', source: 'c1', target: 'c2', confidence: 'verified' }),
      relationship({ id: 'r2', source: 'c1', target: 'c2', confidence: 'inferred' }),
    ],
  })
  const { text } = diagramToMermaid(d)
  assert.ok(text.includes('c1 -->|'))
  assert.ok(text.includes('c1 -.->|'))
  assert.ok(/linkStyle 1 /.test(text)) // index 1 = the second (inferred) relationship
  assert.ok(!/linkStyle 0 /.test(text)) // index 0 = the verified one, never styled as inferred
})

test('the node label includes the component label and a real filename, never an invented one', () => {
  const d = diagram({ components: [component({ id: 'c1', label: 'Users API', source_paths: ['backend/routes/users.js'] })] })
  const { text } = diagramToMermaid(d)
  assert.ok(text.includes('Users API'))
  assert.ok(text.includes('users.js'))
  assert.ok(!text.includes('backend/routes/users.js')) // the node shows a filename, not a full path (paths are for navigation, not the label)
})

test('multiple source paths are summarized, not all spelled out, to keep the node readable', () => {
  const d = diagram({ components: [component({ id: 'c1', source_paths: ['a/one.py', 'a/two.py', 'a/three.py'] })] })
  const { text } = diagramToMermaid(d)
  assert.ok(text.includes('one.py'))
  assert.ok(text.includes('+2 more'))
})

test('escapeMermaidLabel neutralizes characters that could break out of a markdown-string label', () => {
  const escaped = escapeMermaidLabel('a `backtick`, <tag>, a|pipe, and\na newline')
  assert.ok(!escaped.includes('`'))
  assert.ok(!escaped.includes('<'))
  assert.ok(!escaped.includes('>'))
  assert.ok(!escaped.includes('|'))
  assert.ok(!escaped.includes('\n'))
})

test('a label with hostile mermaid-syntax characters cannot break out of its node, subgraph, or edge', () => {
  const hostile = 'x`]end\nsubgraph evil["gotcha'
  const d = diagram({
    groups: [{ id: 'g1', label: hostile, description: '' }],
    components: [component({ id: 'c1', group_id: 'g1', label: hostile })],
    relationships: [relationship({ source: 'c1', target: 'c1', label: hostile })],
  })
  const { text } = diagramToMermaid(d)
  // every opened subgraph must still be closed exactly once per subgraph: no stray "end" was injected by the label
  assert.equal((text.match(/^\s*subgraph /gm) ?? []).length, (text.match(/^\s*end\s*$/gm) ?? []).length)
  assert.ok(!text.includes('\n' + 'subgraph evil'))
})

test('component and group ids that collide after sanitizing still produce distinct mermaid ids', () => {
  const d = diagram({
    groups: [{ id: 'a b', label: 'G', description: '' }],
    components: [component({ id: 'a-b', group_id: 'a b' }), component({ id: 'a.b' })],
  })
  const { text, nodeIdToComponentId } = diagramToMermaid(d)
  const ids = Object.keys(nodeIdToComponentId)
  assert.equal(new Set(ids).size, ids.length) // no two different components share a mermaid id
  assert.equal(text.match(/\bsubgraph\b/g)?.length, 1)
})

test('every component gets a click directive whose argument is always the sanitized node id, never raw model text', () => {
  const d = diagram({ components: [component({ id: 'weird id "with" quotes' })] })
  const { text, nodeIdToComponentId } = diagramToMermaid(d)
  const nodeId = Object.keys(nodeIdToComponentId)[0]
  assert.ok(text.includes(`click ${nodeId} call ${DIAGRAM_CLICK_CALLBACK}("${nodeId}")`))
  assert.ok(/^[A-Za-z0-9_]+$/.test(nodeId)) // safe to embed in a quoted call argument unescaped
})

test('an empty diagram still produces a valid, parseable-shaped flowchart header', () => {
  const { text } = diagramToMermaid(diagram({}))
  assert.equal(text.trim(), 'flowchart LR')
})
