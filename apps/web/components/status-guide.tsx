import { STATUS_HELP } from '@/components/indexing-status'

const TERMS: [string, string][] = [
  ['Queued', STATUS_HELP.queued],
  ['Ingesting', STATUS_HELP.indexing],
  ['Ingested', STATUS_HELP.completed],
  ['Failed', STATUS_HELP.failed],
  ['Semantic index ready', 'Questions are matched by meaning as well as by exact words.'],
  ['Keyword search', 'Questions are matched on exact words and names; the semantic index is not built for this repository.'],
]

/** A collapsed, plain-language key to the badges on repository cards. */
export function StatusGuide() {
  return (
    <details className="group rounded-lg border border-border bg-card/50 px-4 py-3 text-sm">
      <summary className="cursor-pointer select-none rounded text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/40">
        What do the statuses mean?
      </summary>
      <dl className="mt-3 grid gap-x-8 gap-y-2.5 sm:grid-cols-2">
        {TERMS.map(([term, text]) => (
          <div key={term} className="flex flex-col gap-0.5">
            <dt className="font-medium">{term}</dt>
            <dd className="text-xs leading-relaxed text-muted-foreground">{text}</dd>
          </div>
        ))}
      </dl>
    </details>
  )
}
