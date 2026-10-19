'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useEffect, useMemo, useState } from 'react'
import { ChevronRight, File, FileText, Folder, FolderOpen, PanelLeft, Search, SearchX } from 'lucide-react'
import { CodeViewer, parseLineRange } from '@/components/workspace/code-viewer'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { useFile, useFileTree } from '@/lib/api/hooks'
import { ancestorDirectories, LANGUAGE_LABELS } from '@/lib/repo-utils'
import type { FileTreeNode, Repository } from '@/lib/types'
import { cn } from '@/lib/utils'

interface CodeExplorerProps {
  repo: Repository
  file: string | null
  lines: string | null
}

function pickDefaultFile(paths: string[]): string | null {
  return paths.find((p) => /^readme/i.test(p)) ?? paths[0] ?? null
}

function flattenFiles(nodes: FileTreeNode[]): string[] {
  return nodes.flatMap((n) => (n.type === 'file' ? [n.path] : flattenFiles(n.children ?? [])))
}

export function CodeExplorer({ repo, file, lines }: CodeExplorerProps) {
  const router = useRouter()
  const { data: tree, isLoading: treeLoading, error: treeError } = useFileTree(repo)
  const [query, setQuery] = useState('')
  const [treeOpen, setTreeOpen] = useState(false)

  const paths = useMemo(() => (tree ? flattenFiles(tree.tree) : []), [tree])
  const selected = file && paths.includes(file) ? file : pickDefaultFile(paths)
  const { data: source, isLoading: fileLoading, error } = useFile(repo, selected)
  const highlight = parseLineRange(lines)

  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  useEffect(() => {
    if (!selected) return
    setExpanded((prev) => new Set([...prev, ...ancestorDirectories(selected)]))
  }, [selected])

  function open(path: string) {
    setTreeOpen(false)
    router.replace(`/repos/${repo.id}?tab=code&file=${encodeURIComponent(path)}`, { scroll: false })
  }

  function toggle(path: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  const trimmed = query.trim().toLowerCase()
  const results = trimmed ? paths.filter((p) => p.toLowerCase().includes(trimmed)) : null

  const sidebar = (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-border p-2">
        <div className="relative">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search files"
            aria-label="Search files"
            className="h-8 pl-8"
          />
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
        {treeError ? (
          <p className="p-3 text-sm text-destructive">Could not load the file list.</p>
        ) : treeLoading ? (
          <div className="flex flex-col gap-2 p-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-4 w-full" style={{ width: `${90 - (i % 4) * 14}%` }} />
            ))}
          </div>
        ) : results ? (
          results.length === 0 ? (
            <p className="flex items-center gap-2 p-3 text-sm text-muted-foreground">
              <SearchX className="size-4" aria-hidden="true" />
              No files match “{query.trim()}”
            </p>
          ) : (
            <ul>
              {results.map((p) => (
                <li key={p}>
                  <button
                    onClick={() => open(p)}
                    className={cn(
                      'flex w-full flex-col rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent',
                      p === selected && 'bg-accent',
                    )}
                  >
                    <span className="truncate font-medium">{p.split('/').pop()}</span>
                    <span className="truncate font-mono text-xs text-muted-foreground">{p}</span>
                  </button>
                </li>
              ))}
            </ul>
          )
        ) : (
          <TreeList nodes={tree?.tree ?? []} depth={0} expanded={expanded} selected={selected} onToggle={toggle} onOpen={open} />
        )}
      </div>
    </div>
  )

  return (
    <div className="flex h-full min-h-0">
      <aside className="hidden w-72 shrink-0 border-r border-border bg-sidebar/50 md:block" aria-label="File tree">
        {sidebar}
      </aside>

      <Sheet open={treeOpen} onOpenChange={setTreeOpen}>
        <SheetContent side="left" className="w-72 gap-0 p-0" showCloseButton={false}>
          <SheetTitle className="sr-only">Files</SheetTitle>
          <SheetDescription className="sr-only">Browse repository files</SheetDescription>
          {sidebar}
        </SheetContent>
      </Sheet>

      <section className="flex min-w-0 flex-1 flex-col" aria-label="Source preview">
        <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border px-3">
          <Button variant="ghost" size="icon-sm" className="md:hidden" onClick={() => setTreeOpen(true)} aria-label="Open file tree">
            <PanelLeft aria-hidden="true" />
          </Button>
          {selected && (
            <nav aria-label="File path" className="min-w-0 flex-1">
              <ol className="flex items-center gap-1 overflow-x-auto font-mono text-xs whitespace-nowrap text-muted-foreground">
                {selected.split('/').map((part, i, all) => (
                  <li key={i} className="flex items-center gap-1">
                    {i > 0 && <ChevronRight className="size-3 opacity-60" aria-hidden="true" />}
                    <span className={i === all.length - 1 ? 'text-foreground' : undefined}>{part}</span>
                  </li>
                ))}
              </ol>
            </nav>
          )}
          {source && (
            <span className="hidden shrink-0 text-xs text-muted-foreground sm:block">
              {source.languageLabel ?? LANGUAGE_LABELS[source.language]} · {source.lineCount ?? source.content.split('\n').length} lines
            </span>
          )}
        </div>

        {treeError ? (
          <p className="p-6 text-sm text-destructive">Could not load the file list for this repository.</p>
        ) : !treeLoading && paths.length === 0 ? (
          <p className="p-6 text-sm text-muted-foreground">No source files were stored for this repository.</p>
        ) : error ? (
          <p className="p-6 text-sm text-destructive">Could not load this file.</p>
        ) : !selected || (fileLoading && !source) ? (
          <div className="flex flex-col gap-2 p-6">
            {Array.from({ length: 12 }).map((_, i) => (
              <Skeleton key={i} className="h-4" style={{ width: `${30 + ((i * 37) % 60)}%` }} />
            ))}
          </div>
        ) : source ? (
          <>
            <CodeViewer
              key={source.path}
              content={source.content.replace(/\n$/, '')}
              language={source.language}
              highlight={highlight}
              className="min-h-0 flex-1"
            />
            {(source.summary || source.imports.length > 0) && (
              <div className="shrink-0 border-t border-border bg-muted/30 px-4 py-2.5 text-xs text-muted-foreground">
                {source.summary && (
                  <p>
                    <span className="font-medium text-foreground">Sample summary · </span>
                    {source.summary}
                  </p>
                )}
                {source.imports.length > 0 && (
                  <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span>Imports:</span>
                    {source.imports.map((p) => (
                      <Link
                        key={p}
                        href={`/repos/${repo.id}?tab=code&file=${encodeURIComponent(p)}`}
                        className="rounded bg-muted px-1.5 py-0.5 font-mono text-foreground hover:bg-accent"
                      >
                        {p}
                      </Link>
                    ))}
                  </p>
                )}
              </div>
            )}
          </>
        ) : null}
      </section>
    </div>
  )
}

function TreeList({
  nodes,
  depth,
  expanded,
  selected,
  onToggle,
  onOpen,
}: {
  nodes: FileTreeNode[]
  depth: number
  expanded: Set<string>
  selected: string | null
  onToggle: (path: string) => void
  onOpen: (path: string) => void
}) {
  return (
    <ul role={depth === 0 ? 'tree' : 'group'}>
      {nodes.map((node) => {
        const isDir = node.type === 'directory'
        const isOpen = expanded.has(node.path)
        const Icon = isDir ? (isOpen ? FolderOpen : Folder) : /\.(md|txt)$/i.test(node.name) ? FileText : File
        return (
          <li key={node.path} role="treeitem" aria-expanded={isDir ? isOpen : undefined} aria-selected={node.path === selected}>
            <button
              onClick={() => (isDir ? onToggle(node.path) : onOpen(node.path))}
              className={cn(
                'flex h-7 w-full items-center gap-1.5 rounded-md pr-2 text-left text-[13px] hover:bg-accent',
                node.path === selected && 'bg-accent font-medium text-accent-foreground',
              )}
              style={{ paddingLeft: 8 + depth * 14 }}
            >
              {isDir ? (
                <ChevronRight className={cn('size-3.5 shrink-0 text-muted-foreground transition-transform', isOpen && 'rotate-90')} aria-hidden="true" />
              ) : (
                <span className="w-3.5 shrink-0" />
              )}
              <Icon className={cn('size-4 shrink-0', isDir ? 'text-primary/80' : 'text-muted-foreground')} aria-hidden="true" />
              <span className="truncate">{node.name}</span>
            </button>
            {isDir && isOpen && node.children && (
              <TreeList nodes={node.children} depth={depth + 1} expanded={expanded} selected={selected} onToggle={onToggle} onOpen={onOpen} />
            )}
          </li>
        )
      })}
    </ul>
  )
}
