/** The CSRF token of the current session, kept in memory only (never in storage). */
let token: string | null = null

export const getCsrfToken = () => token
export const setCsrfToken = (value: string | null) => {
  token = value
}

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

export const isSafeMethod = (method: string | undefined) => SAFE_METHODS.has((method ?? 'GET').toUpperCase())

/** Request options for the API: always send the session cookie, and add the CSRF header to state-changing requests. */
export function withAuth(init: RequestInit | undefined, csrf: string | null): RequestInit {
  const headers = new Headers(init?.headers)
  if (csrf && !isSafeMethod(init?.method)) headers.set('X-CSRF-Token', csrf)
  return { ...init, headers, credentials: 'include' }
}
