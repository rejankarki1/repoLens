import type { FileTreeNode, SourceFile } from '@/lib/types'

export function buildFileTree(files: Pick<SourceFile, 'path'>[]): FileTreeNode[] {
  const root: FileTreeNode = { name: '', path: '', type: 'directory', children: [] }

  for (const { path } of files) {
    const parts = path.split('/')
    let node = root
    parts.forEach((part, i) => {
      const isFile = i === parts.length - 1
      const childPath = parts.slice(0, i + 1).join('/')
      node.children ??= []
      let child = node.children.find((c) => c.name === part)
      if (!child) {
        child = isFile
          ? { name: part, path: childPath, type: 'file' }
          : { name: part, path: childPath, type: 'directory', children: [] }
        node.children.push(child)
      }
      node = child
    })
  }

  const sort = (nodes: FileTreeNode[]): FileTreeNode[] =>
    nodes
      .sort((a, b) => (a.type === b.type ? a.name.localeCompare(b.name) : a.type === 'directory' ? -1 : 1))
      .map((n) => (n.children ? { ...n, children: sort(n.children) } : n))

  return sort(root.children ?? [])
}

export function ancestorDirectories(path: string): string[] {
  const parts = path.split('/')
  return parts.slice(0, -1).map((_, i) => parts.slice(0, i + 1).join('/'))
}

const GITHUB_URL = /^(?:https?:\/\/)?(?:www\.)?github\.com\/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))\/([A-Za-z0-9._-]{1,100}?)(?:\.git)?\/?$/

export type ParsedRepoUrl = { ok: true; owner: string; name: string } | { ok: false; error: string }

export function parseGitHubUrl(input: string): ParsedRepoUrl {
  const value = input.trim()
  if (!value) return { ok: false, error: 'Enter a GitHub repository URL.' }

  const shorthand = /^([A-Za-z0-9-]+)\/([A-Za-z0-9._-]+)$/.exec(value)
  if (shorthand) return { ok: true, owner: shorthand[1], name: shorthand[2] }

  const match = GITHUB_URL.exec(value)
  if (!match) {
    return { ok: false, error: 'Use the format https://github.com/owner/repository.' }
  }
  return { ok: true, owner: match[1], name: match[2] }
}

export function formatRelativeTime(iso: string | null): string {
  if (!iso) return 'Never'
  const diff = Date.now() - new Date(iso).getTime()
  const minutes = Math.round(diff / 60_000)
  if (minutes < 1) return 'Just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  return `${days}d ago`
}

export function formatNumber(n: number): string {
  return new Intl.NumberFormat('en-US').format(n)
}

export const LANGUAGE_LABELS: Record<SourceFile['language'], string> = {
  typescript: 'TypeScript',
  tsx: 'TypeScript JSX',
  javascript: 'JavaScript',
  python: 'Python',
  json: 'JSON',
  markdown: 'Markdown',
  toml: 'TOML',
  text: 'Plain text',
}
