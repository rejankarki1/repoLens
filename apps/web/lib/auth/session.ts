import { API_URL } from '@/lib/api/config'
import { getCsrfToken, setCsrfToken } from '@/lib/auth/csrf'

export interface AuthUser {
  id: number
  login: string
  name: string | null
  avatar_url: string | null
}

export interface AuthState {
  authenticated: boolean
  user: AuthUser | null
  /** Whether the server has GitHub OAuth credentials; without them sign-in cannot work. */
  loginEnabled: boolean
}

interface ApiMe {
  authenticated: boolean
  user: AuthUser | null
  csrf_token: string | null
  github_login_enabled: boolean
}

/** Who is signed in. Also (re)loads the CSRF token that state-changing requests must send. */
export async function fetchMe(): Promise<AuthState> {
  let response: Response
  try {
    response = await fetch(`${API_URL}/auth/me`, { credentials: 'include', cache: 'no-store' })
  } catch {
    return { authenticated: false, user: null, loginEnabled: true }
  }
  const body = (await response.json().catch(() => null)) as ApiMe | null
  setCsrfToken(body?.authenticated ? (body.csrf_token ?? null) : null)
  return { authenticated: !!body?.authenticated, user: body?.user ?? null, loginEnabled: body?.github_login_enabled ?? true }
}

/** A write must never go out without a token: load it first if the page has not yet. */
export async function ensureCsrfToken(): Promise<string | null> {
  if (!getCsrfToken()) await fetchMe()
  return getCsrfToken()
}

/** The sign-in URL: the browser goes to the API, then GitHub, then back to `next` on this site. */
export function loginUrl(next: string): string {
  return `${API_URL}/auth/github/login?next=${encodeURIComponent(next)}`
}

export async function logout(): Promise<void> {
  const token = await ensureCsrfToken()
  await fetch(`${API_URL}/auth/logout`, {
    method: 'POST',
    credentials: 'include',
    headers: token ? { 'X-CSRF-Token': token } : {},
  }).catch(() => undefined)
  setCsrfToken(null)
}
