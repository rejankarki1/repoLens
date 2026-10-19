import type { Repository, RepositoryDataset } from '@/lib/types'
import { code, file } from '@/lib/mock/helpers'
import { expressDataset } from './express'
import { nextjsDataset } from './nextjs'
import { reactDataset } from './react'

const datasets: Record<string, RepositoryDataset> = {
  'expressjs-express': expressDataset,
  'vercel-next-js': nextjsDataset,
  'facebook-react': reactDataset,
}

const placeholderCache = new Map<string, RepositoryDataset>()

/** Imported repositories without curated mock data get a minimal placeholder index. */
function createPlaceholderDataset(repo: Repository): RepositoryDataset {
  const readme = file({
    path: 'README.md',
    content: code(`
      # ${repo.name}

      ${repo.description || 'No description provided.'}

      > This repository was imported in demo mode. RepoLens generated a
      > placeholder index because no source data is available offline.
    `),
  })

  return {
    isPlaceholder: true,
    files: [readme],
    insights: {
      summary:
        'Architecture analysis is unavailable for this repository in demo mode. Connect the RepoLens backend to generate a real summary from the indexed source.',
      entryPoints: [],
      keyModules: [{ path: 'README.md', name: 'README.md', description: 'Project readme.' }],
    },
    answers: [],
    fallbackAnswer: {
      content: `This repository was imported in **demo mode**, so only a placeholder \`README.md\` is indexed.

Connect the RepoLens backend to answer questions grounded in the real source code.`,
      citations: [{ path: 'README.md', startLine: 1, endLine: 6 }],
    },
  }
}

export function getDataset(repo: Repository): RepositoryDataset {
  const curated = datasets[repo.id]
  if (curated) return curated
  let placeholder = placeholderCache.get(repo.id)
  if (!placeholder) {
    placeholder = createPlaceholderDataset(repo)
    placeholderCache.set(repo.id, placeholder)
  }
  return placeholder
}
