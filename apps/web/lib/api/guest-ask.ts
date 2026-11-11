import { request } from '@/lib/api/client'

interface ApiGuestAsk {
  enabled: boolean
  limit: number
  remaining: number
  resets_in_seconds: number | null
}

export interface GuestAsk {
  /** The server lets visitors ask live questions about this repository. */
  enabled: boolean
  limit: number
  /** Questions this visitor can still ask today (the smallest of the visitor, network and all-visitors allowances). */
  remaining: number
  resetsInSeconds: number | null
}

/** Whether visitors may ask live questions here, and how many are left. Counts nothing on the server. */
export async function getGuestAsk(repoId: string): Promise<GuestAsk> {
  const body = await request<ApiGuestAsk>(`/repositories/${encodeURIComponent(repoId)}/guest-ask`)
  return { enabled: body.enabled, limit: body.limit, remaining: body.remaining, resetsInSeconds: body.resets_in_seconds }
}
