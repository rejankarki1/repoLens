# Production security considerations

What is protected, what the operator must configure, and what is deliberately not covered. Design background: [auth-design.md](auth-design.md).

## What the application enforces

- **Identity** comes only from a server-side session. The browser never sends, and the server never trusts, a user id.
- **Sessions**: 256-bit random tokens, only their SHA-256 stored, `HttpOnly`, `SameSite=Lax`, `Secure` and `__Host-` prefixed in production, absolute and idle expiry, logout and logout-everywhere.
- **CSRF**: every state-changing request needs the per-session token (`X-CSRF-Token`) and a known `Origin`; `SameSite=Lax` and a strict CORS allow-list sit underneath.
- **OAuth**: authorization code with PKCE, single-use state bound to the browser, no scope, redirect targets limited to the configured web URL plus a validated relative path, token used once and revoked.
- **Authorization**: workspace membership for repository data, ownership for conversations, an explicit allow-list for guest access. Unknown or foreign ids answer 404 (signed in) or 401 (visitor), never a hint.
- **Usage limits** (atomic, in Redis): AI questions per user and per day, burst, a global daily budget and global concurrency; imports per hour and concurrently; embedding and analysis jobs; guest reads and sign-in traffic per IP. A failed answer does not use the daily quota. If Redis is down, expensive actions are refused (503).
- **Input**: GitHub owner/repository names follow GitHub's rules; URLs with other hosts, credentials or ports are refused; NUL characters are rejected; downloads only use the configured GitHub API base with names built from a validated pair; archive size, file count and timeouts are bounded.
- **Responses and logs**: security headers on every response (HSTS and a locked-down CSP in production), API docs disabled in production, OAuth `code`/`state` redacted from access logs, generic bodies for unexpected errors, provider keys held as secrets and never returned.
- **Start-up check**: with `ENVIRONMENT=production` the API refuses to start if cookies are not `Secure`, the web or API URL is not https, CORS origins are wildcard or http, OAuth credentials are missing, or the database still uses the development password.

## What the operator must do

1. **Same-site deployment.** Serve the web app and the API from the same registrable domain (for example `app.example.com` and `api.example.com`). A `SameSite=Lax` cookie is not sent on cross-site `fetch`, so a different registrable domain (for example `*.vercel.app` talking to `*.onrender.com`) will not stay signed in. If that is unavoidable, put the API behind the web domain with a reverse-proxy path instead of changing the cookie policy.
2. **HTTPS everywhere**, terminated at a proxy you control.
3. **Configuration**: `ENVIRONMENT=production`; `WEB_BASE_URL`, `WEB_ORIGINS` and `API_PUBLIC_URL` as exact https URLs; a production-only OAuth App (callback `<API_PUBLIC_URL>/auth/github/callback`); `GITHUB_OAUTH_CLIENT_ID/SECRET` from the platform's secret store, never from the repository or a `NEXT_PUBLIC_*` variable.
4. **Proxy awareness.** Behind a reverse proxy or load balancer, set `TRUSTED_PROXIES` to its address(es), or every visitor will appear to come from the proxy and share one guest/sign-in allowance. Leave it empty if clients connect directly.
5. **Database and Redis**: strong unique passwords, private networking, TLS if they cross a network, backups for PostgreSQL. Redis holds quota counters, OAuth state and the job queue: enable persistence if losing in-flight jobs matters. Losing counters only resets limits.
6. **Migrations**: run `alembic upgrade head` on release, after a backup. Download the tree-sitter grammars at image build time (`ensure_grammars()`).
7. **Tune the limits** (`AI_*`, `IMPORT_*`, ...) to your provider budget. `AI_GLOBAL_DAILY_LIMIT` is the hard ceiling on daily AI spend through this app; set provider-side spend caps too.
8. **Showcase**: list only repositories you want to be fully public in `GUEST_SHOWCASE_REPOSITORIES`; re-check the list when you rename or replace them.
9. **Monitoring**: watch 401/403/429 rates, `/health/ready`, worker queue depth and provider errors. Access logs are redacted, but treat all logs as sensitive.

## Deliberate limits of this design

- **Many accounts, many quotas.** Limits are per user. Someone who creates many GitHub accounts gets many allowances; the global daily budget and concurrency limit bound the total cost, and per-IP limits cover only guests and sign-in. There is no CAPTCHA, email verification or account-age check.
- **Public repositories only.** No private repository access exists, and no GitHub token is retained, so there is nothing to leak there.
- **Shared repository data.** Users who analyze the same public repository share one stored snapshot. A re-ingestion by any member refreshes it for everyone. Repository content is public by definition; personal data is not shared.
- **No account deletion or data export yet.** Deleting a user cascades to sessions, memberships and conversations at the database level, but there is no user-facing flow.
- **No rate limit on `/auth/me` or other cheap authenticated reads**, and signed-in reads of showcase repositories are not metered per IP.
- **Real OAuth not exercised.** Automated and browser tests use a fake GitHub that enforces PKCE. The first run against a real OAuth App should be watched.
- **CSP and HSTS apply to the API only.** Security headers for the Next.js app (CSP, frame ancestors) are not configured and belong in the web host's configuration.
