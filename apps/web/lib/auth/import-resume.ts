/** Remembering the repository a visitor pasted while they sign in with GitHub, and picking it up again afterwards. */

const KEY = 'import'

/** Where the API sends the browser after sign-in so the pasted repository is imported automatically. */
export function resumePath(githubUrl: string): string {
  return `/repositories?${KEY}=${encodeURIComponent(githubUrl)}`
}

/** The pasted repository URL carried by the page URL, if any (length-bounded; the server validates it again). */
export function pendingImport(search: string): string | null {
  const value = new URLSearchParams(search).get(KEY)
  return value && value.length <= 300 ? value : null
}

const AUTH_ERRORS: Record<string, string> = {
  access_denied: 'GitHub sign-in was cancelled. Nothing was changed.',
  invalid_state: 'The sign-in could not be verified (it may have expired). Please try again.',
  github_unavailable: 'GitHub could not be reached to confirm your identity. Please try again in a moment.',
  oauth_failed: 'GitHub sign-in did not complete. Please try again.',
  rate_limited: 'Too many sign-in attempts from this network. Wait a minute and try again.',
  unavailable: 'Sign-in is temporarily unavailable. Please try again shortly.',
}

/** The message for `?auth_error=<code>`; unknown codes get a generic one rather than being echoed. */
export function authErrorMessage(search: string): string | null {
  const code = new URLSearchParams(search).get('auth_error')
  return code ? (AUTH_ERRORS[code] ?? 'Sign-in failed. Please try again.') : null
}
