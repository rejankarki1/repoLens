# Authentication and authorization design

Status: implemented in Phase 2. This document is the design the code follows; the sections at the end say what is verified and what is not.

## Starting point (Phase 1)

- `users` (email, unique, not null) and `github_accounts` (user, numeric GitHub id, login) exist but only one row is ever used: the local default user `local@repolens.local`, created on first use. Every conversation belongs to it.
- `repositories` are global. Any client could list, read, import, re-ingest, search, analyze or delete conversations of any repository by id.
- There is no session, no CSRF protection, no per-user quota, and no Redis rate limiting. Provider 429s are only translated into HTTP 429.
- The web app talks to the API with `fetch` and no credentials.

## Goals and non-goals

Goals: GitHub sign-in, server-side sessions, per-user workspaces and conversations, a restricted guest showcase, server-side quotas, and no loss of existing data.

Non-goals: private repositories, GitHub write access, teams or sharing, billing, JWTs, any change to RAG, citation validation, snapshot scoping or Tree-sitter safeguards.

## Schema changes (two additive migrations)

| Migration | Change |
| --- | --- |
| `c1a7e3d9f402_add_auth_sessions` | New `sessions` table. `github_accounts` gains `display_name`, `avatar_url`, `created_at`, `last_login_at`. `users.email` becomes nullable (we do not request the `user:email` scope, so GitHub users have no email; unique stays, and PostgreSQL allows many NULLs). |
| `d2b8f4e0a513_add_user_repositories` | New `user_repositories` (workspace membership). If the local default user exists, it is made a member of every existing repository so that development data stays reachable. |

`sessions`: `id`, `user_id` (FK, cascade), `token_hash` (SHA-256 hex of the cookie value, unique), `csrf_token`, `created_at`, `last_seen_at`, `expires_at`, `revoked_at`, `user_agent` (truncated). Raw session tokens are never stored.

`user_repositories`: `user_id` + `repository_id` (composite primary key, both FKs cascade), `added_at`. Repositories, files, chunks, embeddings and architecture analyses stay shared: a public snapshot is stored once however many users add it.

Unchanged: `repositories`, `source_files`, `code_chunks`, `code_embeddings`, `architecture_analyses`, `conversations`, `messages`. Conversations already have `user_id`; they simply stop defaulting to one user.

## Existing development data

The local default user keeps its row, its conversations and (after the migration) its repository memberships. Nobody can sign in as it. A newly registered GitHub user is never given this data automatically. To take it over, run `python -m apps.api.scripts.claim_local_data --github-login <login>` after that GitHub user has signed in once; it moves the conversations and memberships in one transaction and is safe to repeat. See `docs/auth-migration.md`.

## Sign-in flow

GitHub OAuth authorization code flow, controlled by the backend.

1. `GET /auth/github/login?next=<relative path>`: validates `next` (must be a relative path of this app: starts with a single `/`, no scheme, no backslash, bounded length), creates a random `state` and a PKCE verifier, stores `{verifier, next}` in Redis for 10 minutes under the state, sets a short-lived `HttpOnly`, `SameSite=Lax` cookie containing the same state (binds the flow to this browser), and redirects to GitHub with `client_id`, `redirect_uri`, `state`, `code_challenge` (S256) and no `scope`. No scope means public profile data only: no repository access of any kind.
2. GitHub redirects to `GET /auth/github/callback?code&state`. The callback rejects the request unless the state query value equals the state cookie and a Redis record exists (the record is deleted on first read, so it is single use). `error=access_denied` and any other provider error redirect to the web app with `?auth_error=<code>`; nothing is created.
3. The server exchanges the code (with the PKCE verifier and the client secret) for an access token, calls `GET /user` once, and revokes the token best-effort. The token is never stored, logged or sent to the browser.
4. The numeric GitHub id is the identity. The `github_accounts` row is created or updated (login, name, avatar); a `users` row is created on first sign-in.
5. A new session is created (never reuses an old one), the session cookie is set, and the browser is redirected to `WEB_BASE_URL + next`.

Redirect safety: the only redirect targets are GitHub's authorize URL and `WEB_BASE_URL` plus a validated relative path. `redirect_uri` is configured, not taken from the request.

## Sessions

- 256-bit random token, URL-safe; the database stores only its SHA-256.
- Cookie: `HttpOnly`, `Path=/`, `SameSite=Lax`, `Secure` in production, no `Domain` attribute (host-only). In production the name carries the `__Host-` prefix, which browsers only accept with `Secure`, `Path=/` and no `Domain`.
- Lifetime: absolute (`SESSION_TTL_SECONDS`, default 14 days) and idle (`SESSION_IDLE_SECONDS`, default 7 days); `last_seen_at` is refreshed at most every 5 minutes. Expired or revoked sessions resolve to "signed out".
- `POST /auth/logout` revokes the current session and clears the cookie; `POST /auth/logout-all` revokes every session of the user.
- `GET /auth/me` returns the user (or `authenticated: false`), whether GitHub sign-in is configured, and the CSRF token for the current session.

## CSRF

Cookie sessions need CSRF protection for state-changing requests. Three layers:

1. `SameSite=Lax` keeps the cookie off cross-site POSTs.
2. Every `POST`, `PATCH` and `DELETE` that uses a session must send `X-CSRF-Token` equal to the per-session token (constant-time compare). The token comes from `/auth/me`, is kept in memory by the web app, and is not readable by other origins.
3. If an `Origin` header is present it must be in `WEB_ORIGINS`.

Requests that are rejected for CSRF get 403 and change nothing. `GET` endpoints never change state.

Deployment: the web app and API must be same-site (for example `app.example.com` and `api.example.com`), because a `SameSite=Lax` cookie is not sent on cross-site `fetch`. Locally use `localhost` for both (`127.0.0.1` and `localhost` are different sites).

## Authorization model

Identity always comes from the session cookie. No endpoint accepts a user id from the client.

| Resource | Rule |
| --- | --- |
| Repository reads (metadata, files, file content, ingestion status, architecture) | Member of the repository, or the repository is in the guest showcase. Anyone else gets 404 (signed in) or 401 (signed out), never a hint about whether an id exists. |
| Repository list | Signed in: the user's own workspace. Signed out: the showcase. |
| Import (`POST /repositories`) | Signed in. Adds the user to the workspace; a repository someone else already imported is reused, not copied. |
| Re-ingest, build embeddings, request architecture analysis | Member only, CSRF, rate limited. |
| Search and search-index status | Member only (hybrid search spends embedding quota). |
| Conversations and messages | Member of the repository and owner of the conversation. A missing conversation and someone else's conversation both return 404. |

## Guest showcase

An explicit allow-list, `GUEST_SHOWCASE_REPOSITORIES` (comma separated `owner/name`). Nothing is public unless listed. Guests can list the showcase, open the overview, Code Explorer and Architecture Explorer, and read the curated example answers (real saved answers with their citations, see `scripts/publish_showcase_answers.py`), and nothing else: no imports, no re-analysis, no deletion, no search, no AI, no conversations. A signed-in user may view showcase repositories read-only and adds one to their workspace (no new indexing) to ask questions. Guest requests are rate limited per IP.

## Usage protection

All limits are settings (`AI_*`, `IMPORT_*`, `GUEST_*`, `AUTH_*`); defaults are in `config.py` and `.env.example`. State lives in Redis, changed only by Lua scripts so that check-and-update is atomic:

- Fixed-window counters (`reserve`, with refund) for per-user daily AI questions (default 20), per-user burst (default 5 per minute), global daily AI questions, per-user imports per hour, per-IP guest reads, per-IP login starts.
- Lease semaphores for global AI concurrency and (database row lock) concurrent imports per user.
- Quota policy for AI questions: the daily and global counters are reserved before any provider call and refunded when no answer is saved (provider error, truncation, interruption, client stop). Burst attempts are not refunded. Exceeding a limit returns HTTP 429 with `Retry-After`.
- Client IP: `X-Forwarded-For` is honoured only when the direct peer is in `TRUSTED_PROXIES`; otherwise the socket address is used.
- Import hardening: owner and repository names are validated (GitHub charset and length, no `.` or `..`); downloads use only `github_api_base_url` with names built from the validated pair; existing archive size, file count and timeout limits stay; a duplicate active ingestion returns 409.

If Redis is unavailable, protected expensive operations fail closed with 503 (limits cannot be enforced). Read-only browsing is not blocked by Redis.

## Hardening

Production start-up validation (secure cookies, HTTPS web URL, OAuth credentials, explicit CORS origins); restrictive CORS (methods and headers listed, `Retry-After` exposed); security headers; API docs disabled in production; access logs redact OAuth `code` and `state`; generic error bodies; unchanged provider-key handling (`SecretStr`).

## Endpoints

| Method | Path | Who | Notes |
| --- | --- | --- | --- |
| GET | `/auth/github/login?next=` | anyone | Starts sign-in (per-IP limited). `next` is a validated relative path. |
| GET | `/auth/github/callback` | GitHub's redirect | Validates state, creates the session, redirects to the web app (`?auth_error=` on failure). |
| GET | `/auth/me` | anyone | `{authenticated, user, csrf_token, github_login_enabled}`; never cached. |
| POST | `/auth/logout`, `/auth/logout-all` | signed in + CSRF | 204; the cookie is cleared. |
| GET | `/repositories` | anyone | Your workspace; for a visitor, the showcase. Each item has `viewer_access` (`member` or `guest`). |
| POST | `/repositories` | signed in + CSRF | Adds a public repository: 201 if new, 200 if already stored (nothing downloaded again). |
| DELETE | `/repositories/{id}` | member + CSRF | Removes it from your workspace with your conversations about it. The stored copy is deleted only if nobody else is a member and it is not in the showcase; 409 while ingestion (or, if the copy would be deleted, other jobs) is running. GitHub is never contacted. |
| GET | `/repositories/{id}` and its `files`, `files/content`, `ingestion-status`, `architecture*`, `showcase-answers` | member, or anyone for showcase repositories | Read-only. `showcase-answers` are curated, saved answers: no AI call. |
| POST | `/repositories/{id}/ingestions`, `/embeddings`, `/architecture` | member + CSRF | Background jobs; hourly and concurrency limits. |
| GET | `/repositories/{id}/search`, `/search-index` | member | Hybrid search spends embedding quota. |
| * | `/repositories/{id}/conversations...` | member (owner of the conversation) + CSRF for writes | Questions are subject to the AI quotas. |

Status codes: 401 not signed in; 403 missing/invalid CSRF token, or a showcase repository you have not added; 404 not found *or not yours*; 429 a usage limit (with `Retry-After` and `X-RateLimit-Scope`: `burst`, `daily`, `global`, `busy`, `import`, `import_concurrency`, `architecture`, `embeddings`, `guest`); 503 limits cannot be enforced.

## Verified and not verified

- **Automated, no credentials**: unit and integration tests for sign-in (mocked GitHub identity), sessions, CSRF, ownership, guests, limits (against real Redis for atomicity) and hardening; migration tests on disposable databases; a browser smoke test against a fake GitHub that enforces PKCE. See `docs/release-baseline.md` for the counts.
- **Rehearsed**: both migrations and the claim command on a copy of a real development database.
- **Not verified**: a real GitHub OAuth App (needs your credentials; setup is in the README); deployment on a real domain with HTTPS and a proxy; behaviour of `__Host-` cookies in production browsers; real provider rate limits.
