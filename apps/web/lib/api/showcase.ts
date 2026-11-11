import { request } from '@/lib/api/client'
import { toCitation, type ChatTurnMessage } from '@/lib/api/chat'

type ApiCitation = Parameters<typeof toCitation>[0]

interface ApiShowcaseAnswer {
  id: number
  question: string
  answer: string
  citations: ApiCitation[]
}

export interface ShowcaseExample {
  id: number
  question: ChatTurnMessage
  answer: ChatTurnMessage
}

/** The curated answers published for a showcase repository: real saved answers, served without any AI call. */
export async function getShowcaseAnswers(repoId: string): Promise<ShowcaseExample[]> {
  const rows = await request<ApiShowcaseAnswer[]>(`/repositories/${encodeURIComponent(repoId)}/showcase-answers`)
  return rows.map((row) => ({
    id: row.id,
    question: { id: `q${row.id}`, role: 'user', content: row.question, citations: [], status: 'complete' },
    answer: { id: `a${row.id}`, role: 'assistant', content: row.answer, citations: row.citations.map(toCitation), status: 'complete' },
  }))
}
