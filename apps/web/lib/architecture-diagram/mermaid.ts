/**
 * Deterministic `ArchitectureDiagram` (validated, backend-sealed) -> Mermaid flowchart source.
 *
 * Pure and deterministic: the same diagram always produces the same text (stable ordering, no Date.now()
 * or Math.random() anywhere). Groups become `subgraph`s, components become shaped nodes, relationships
 * become directional edges — solid for `confidence: "verified"`, dashed for `"inferred"` — matching the
 * acceptance criterion that a reader can tell a real import from an AI-inferred runtime relationship at a
 * glance, not only by hovering.
 *
 * Every label (group/component/relationship text, and every real file path shown in a node) passes
 * through `escapeMermaidLabel` before being placed inside a Mermaid markdown-string (`` `...` ``), which
 * is the one place a model-controlled string reaches the diagram source we hand to `mermaid.render()`.
 * Backtick, angle-bracket and pipe characters are neutralized so a crafted label can never break out of
 * its node/edge syntax into another diagram statement. React's own escaping covers everything rendered as
 * plain JSX text elsewhere (tooltips, the file list); only this function's output is ever close to the
 * mermaid *source text* a label could otherwise inject into.
 */

import type { ArchitectureDiagram, ComponentKind, DiagramComponent } from './types.ts'

export interface MermaidDiagram {
  /** Mermaid flowchart source, ready for `mermaid.render()`. */
  text: string
  /** Mermaid node id -> original `DiagramComponent.id`, for wiring click-to-navigate after rendering. */
  nodeIdToComponentId: Record<string, string>
}

type Category = 'process' | 'worker' | 'ui' | 'data' | 'external' | 'support'

const CATEGORY_BY_KIND: Record<ComponentKind, Category> = {
  service: 'process', api: 'process', cli: 'process',
  worker: 'worker', job: 'worker', pipeline: 'worker',
  ui: 'ui', page: 'ui',
  database: 'data', store: 'data',
  external_service: 'external',
  library: 'support', other: 'support',
}

/** Fill/stroke/text per category, light and dark — soft pastel fill with a saturated stroke, legible in
 * both the app's themes (see `components/theme-provider.tsx`); chosen independently of Mermaid's own
 * built-in themes so the diagram matches RepoLens's palette rather than Mermaid's defaults. Workers get
 * their own hue (teal), distinct from request-serving services (blue) — background processing reads
 * differently from a synchronous API at a glance, which is the point of a "distinct styling" requirement. */
const PALETTE: Record<'light' | 'dark', Record<Category, { fill: string; stroke: string; text: string }>> = {
  light: {
    process: { fill: '#DBEAFE', stroke: '#2563EB', text: '#1E3A8A' },
    worker: { fill: '#CCFBF1', stroke: '#0D9488', text: '#134E4A' },
    ui: { fill: '#EDE9FE', stroke: '#7C3AED', text: '#4C1D95' },
    data: { fill: '#D1FAE5', stroke: '#059669', text: '#064E3B' },
    external: { fill: '#FEF3C7', stroke: '#D97706', text: '#78350F' },
    support: { fill: '#F1F5F9', stroke: '#64748B', text: '#334155' },
  },
  dark: {
    process: { fill: '#1E3A5F', stroke: '#60A5FA', text: '#DBEAFE' },
    worker: { fill: '#134E4A', stroke: '#2DD4BF', text: '#CCFBF1' },
    ui: { fill: '#3B2A5E', stroke: '#A78BFA', text: '#EDE9FE' },
    data: { fill: '#0F3D2E', stroke: '#34D399', text: '#D1FAE5' },
    external: { fill: '#4A3410', stroke: '#FBBF24', text: '#FEF3C7' },
    support: { fill: '#1E293B', stroke: '#94A3B8', text: '#E2E8F0' },
  },
}

const CATEGORY_LABEL: Record<Category, string> = {
  process: 'Service / API',
  worker: 'Background worker / job',
  ui: 'UI',
  data: 'Database / store',
  external: 'External service',
  support: 'Library / other',
}

/** Node shape per component kind, independent of color: a database/store reads as a cylinder, an external
 * service as a hexagon, a UI/page as a pill, a background worker/job as a subroutine (double-struck side
 * borders, reads as "a process that runs on its own"), everything else as a plain rectangle. `token` is a
 * complete, ready-to-embed label token from `mdString` (quotes and backticks already included). */
function shape(kind: ComponentKind, token: string): string {
  if (kind === 'database' || kind === 'store') return `[(${token})]`
  if (kind === 'external_service') return `{{${token}}}`
  if (kind === 'ui' || kind === 'page') return `([${token}])`
  if (kind === 'worker' || kind === 'job' || kind === 'pipeline') return `[[${token}]]`
  return `[${token}]`
}

/** Neutralizes the characters that could otherwise break out of a mermaid markdown-string label
 * (backtick closes it; `<`/`>` can start an HTML/foreignObject-ish tag the sanitizer may not expect in
 * every context; `|` is the edge-label delimiter outside of markdown-strings). Everything else, including
 * quotes and parentheses, is safe inside the backtick-delimited form used below. */
export function escapeMermaidLabel(text: string): string {
  return text
    .replace(/`/g, "'")
    .replace(/</g, '‹')
    .replace(/>/g, '›')
    .replace(/\|/g, '❘')
    .replace(/\r?\n/g, ' ')
}

/** A complete, ready-to-embed label token: `` "`line1<br/>line2`" `` — the quoted-markdown-string form
 * mermaid accepts in a node shape, a subgraph title, or an edge label, which tolerates punctuation
 * (quotes, parentheses, commas) that would otherwise need per-context escaping. */
function mdString(lines: string[]): string {
  const safe = lines.filter(Boolean).map(escapeMermaidLabel).join('<br/>')
  return `"\`${safe}\`"`
}

/** Stable, collision-free mermaid node/subgraph ids: alphanumeric/underscore only, never starting with a
 * digit, and de-duplicated deterministically (by first-seen order) if two different real ids collide
 * after sanitizing — so two different `Component.id`s can never silently merge into one Mermaid node. */
function makeIdSanitizer() {
  const used = new Set<string>()
  const seen = new Map<string, string>()
  return (raw: string): string => {
    const existing = seen.get(raw)
    if (existing) return existing
    let base = raw.replace(/[^A-Za-z0-9_]+/g, '_').replace(/^_+/, '')
    if (!base || /^[0-9]/.test(base)) base = `n_${base}`
    let candidate = base
    let suffix = 1
    while (used.has(candidate)) candidate = `${base}_${suffix++}`
    used.add(candidate)
    seen.set(raw, candidate)
    return candidate
  }
}

function basename(path: string): string {
  const parts = path.split('/')
  return parts[parts.length - 1] || path
}

/** The node label: the AI's functional name, plus (when it has one) a real filename — satisfying "render
 * real filenames", not just an invented component name, directly on the diagram. */
function componentLabel(c: DiagramComponent): string {
  const lines = [c.label]
  if (c.source_paths.length === 1) lines.push(basename(c.source_paths[0]))
  else if (c.source_paths.length > 1) lines.push(`${basename(c.source_paths[0])} +${c.source_paths.length - 1} more`)
  return mdString(lines)
}

export function diagramToMermaid(
  diagram: ArchitectureDiagram,
  theme: 'light' | 'dark' = 'light',
  direction: 'LR' | 'TD' = 'LR',
): MermaidDiagram {
  const sanitize = makeIdSanitizer()
  const palette = PALETTE[theme]
  const lines: string[] = [`flowchart ${direction}`]
  const nodeIdToComponentId: Record<string, string> = {}

  const byGroup = new Map<string | null, DiagramComponent[]>()
  for (const c of diagram.components) {
    const key = diagram.groups.some((g) => g.id === c.group_id) ? c.group_id : null
    const list = byGroup.get(key) ?? []
    list.push(c)
    byGroup.set(key, list)
  }

  for (const group of diagram.groups) {
    const members = byGroup.get(group.id) ?? []
    if (members.length === 0) continue
    const groupId = sanitize(`group_${group.id}`)
    lines.push(`  subgraph ${groupId} [${mdString([group.label])}]`)
    for (const c of members) {
      const nodeId = sanitize(c.id)
      nodeIdToComponentId[nodeId] = c.id
      lines.push(`    ${nodeId}${shape(c.kind, componentLabel(c))}`)
    }
    lines.push('  end')
  }
  for (const c of byGroup.get(null) ?? []) {
    const nodeId = sanitize(c.id)
    nodeIdToComponentId[nodeId] = c.id
    lines.push(`  ${nodeId}${shape(c.kind, componentLabel(c))}`)
  }

  const linkStyles: string[] = []
  diagram.relationships.forEach((r, index) => {
    const sourceId = sanitize(r.source)
    const targetId = sanitize(r.target)
    const arrow = r.confidence === 'verified' ? '-->' : '-.->'
    const label = mdString([r.label])
    lines.push(`  ${sourceId} ${arrow}|${label}| ${targetId}`)
    if (r.confidence !== 'verified') linkStyles.push(`  linkStyle ${index} stroke:${palette.support.stroke},stroke-width:1.5px`)
  })
  lines.push(...linkStyles)

  const categoriesUsed = new Set(diagram.components.map((c) => CATEGORY_BY_KIND[c.kind] ?? 'support'))
  for (const category of categoriesUsed) {
    const colors = palette[category]
    lines.push(`  classDef cat_${category} fill:${colors.fill},stroke:${colors.stroke},color:${colors.text},stroke-width:1.5px`)
  }
  for (const c of diagram.components) {
    const nodeId = sanitize(c.id)
    lines.push(`  class ${nodeId} cat_${CATEGORY_BY_KIND[c.kind] ?? 'support'}`)
  }

  // Mermaid's documented click-interaction mechanism (requires `securityLevel: "loose"` — see
  // `DIAGRAM_CLICK_CALLBACK`'s doc comment for why that is safe here). The argument is always the
  // mermaid-sanitized node id, never the model-chosen component id: `sanitize()` guarantees it matches
  // `[A-Za-z0-9_]+`, so it can never contain a quote or paren that could break out of `call fn("...")`.
  // The click handler looks the real component (and its validated source_paths) back up via
  // `nodeIdToComponentId`, which this function also returns.
  for (const c of diagram.components) {
    const nodeId = sanitize(c.id)
    lines.push(`  click ${nodeId} call ${DIAGRAM_CLICK_CALLBACK}("${nodeId}")`)
  }

  return { text: lines.join('\n'), nodeIdToComponentId }
}

/**
 * Name of the `window`-scoped callback Mermaid's `click ... call` directive invokes. `securityLevel:
 * "loose"` is required for `click call` to run at all; that is safe here specifically because every label
 * placed in the diagram source already had `<`/`>` replaced with look-alike characters by
 * `escapeMermaidLabel` before reaching Mermaid, so "loose" mode's more permissive HTML handling never
 * actually sees a real tag to permit.
 */
export const DIAGRAM_CLICK_CALLBACK = '__repolensArchDiagramNodeClick'

export function legendEntries(theme: 'light' | 'dark' = 'light'): { category: Category; label: string; fill: string; stroke: string }[] {
  const palette = PALETTE[theme]
  return (Object.keys(CATEGORY_LABEL) as Category[]).map((category) => ({
    category, label: CATEGORY_LABEL[category], fill: palette[category].fill, stroke: palette[category].stroke,
  }))
}
