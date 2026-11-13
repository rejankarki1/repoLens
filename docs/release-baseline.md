# RepoLens release baseline

A record of what RepoLens was and how it was verified on 2026-11-01, before the first deployment: an application with GitHub sign-in, per-user workspaces and usage limits, running locally. It has since been deployed (see [deployment.md](deployment.md)) and the Architecture tab now shows the AI-generated diagram ([architecture-diagram.md](architecture-diagram.md)); the figures below are not updated. Updated for Phase 2 (authentication and security); the Milestone 0 and 1 results it replaces are in Git history and in [verification-m1.md](verification-m1.md).

## What exists

RepoLens ingests a public GitHub repository, indexes its code, answers questions about it with citations, and maps its module dependencies.

| Area | What is implemented |
| --- | --- |
| Ingestion | Import a public GitHub repository by URL. The API enqueues a job; a Redis/RQ worker downloads the archive, stores the files, and records progress. Jobs are retried once for transient failures, heartbeat while running, and stale jobs are recovered. |
| Parsing | Tree-sitter (`tree-sitter-language-pack`) parses Python, JavaScript, TypeScript and TSX into structure-aware chunks. Other files are stored and indexed as plain text. |
| Retrieval | PostgreSQL full-text search plus pgvector similarity, fused with Reciprocal Rank Fusion, scoped to the repository's current commit. Embeddings come from Gemini (`gemini-embedding-001`). Without a key, search is keyword-only. |
| AI answers | Retrieval-augmented generation with Gemini (default) or Groq. Citations are rebuilt from the retrieved chunks and validated; markers that match no source are dropped. Answers stream to the browser. |
| Chat | Source-grounded Ask AI with saved conversation history: automatic titles, rename, delete, and clear history per repository. |
| Citation previews | Clicking a citation opens the cited file with the lines highlighted, beside the chat on wide screens and in a drawer on narrow ones, with a link to the full Code Explorer. |
| Code Explorer | File tree, source viewer, line deep links, and a per-file Dependencies panel (what the file imports and what imports it). |
| Architecture Explorer | At the time: a deterministic, evidence-linked dependency graph of modules and files with filters, search, and drill-down. Every edge links to the import statement behind it. Technologies and architecture areas are inferred and labelled as such. No AI provider is involved. (The tab has since been replaced by the AI-identified diagram; the deterministic analysis still runs and feeds the Overview tab and the per-file Dependencies panel.) |
| Overview | Real file, directory, line and language statistics and a summary of the stored architecture analysis. |
| Authentication | GitHub OAuth (authorization code with PKCE, no scope), server-side sessions in HttpOnly cookies, CSRF protection, logout. See [auth-design.md](auth-design.md). |
| Workspaces and ownership | Each user has a private workspace of repositories and private conversations; repository data is shared between users of the same public repository. Every endpoint checks membership or ownership. |
| Guest showcase | Visitors browse an explicit allow-list of pre-indexed repositories read-only (overview, source, architecture); no imports, search, AI or conversations. |
| Usage limits | Atomic per-user and global limits on AI questions, imports, embedding and analysis jobs, and per-IP limits for guests and sign-in, enforced in Redis. See [security.md](security.md). |

Detail: [rag.md](rag.md), [search.md](search.md), [architecture.md](architecture.md), [erd.md](erd.md).

### Stack

FastAPI, SQLAlchemy (async), Alembic, PostgreSQL 17 with pgvector 0.8.7, Redis 7.4, RQ. Next.js 16, React 19, Tailwind 4, SWR, React Flow. Python 3.14, Node 22.18 or newer.

## Verified results

Run on 2026-11-01 on the development machine, and for the backend and frontend jobs also on a clean Linux container (fresh Python 3.14 / Node 22, empty pgvector PostgreSQL and Redis, no `.env`, dependencies from the repository's own requirements and lockfile).

| Check | Result |
| --- | --- |
| Backend tests (`pytest`) | **767 passed**, 2 warnings (third-party deprecation notices). 766 passed on clean Linux before the final isolation tests were added. |
| Frontend unit tests (`npm test`) | **49 passed**, 0 failed |
| TypeScript (`tsc --noEmit`) | clean |
| ESLint (`--max-warnings 0`) | 0 errors, 0 warnings |
| Next.js production build | succeeds |
| Alembic | `upgrade head` on an empty database applies all 10 migrations, `check` reports no drift, `downgrade base` then `upgrade head` round-trips. The two authentication migrations are also tested from a seeded Phase 1 schema, and were rehearsed with the claim command on a copy of a real development database (data preserved). |
| Browser smoke test | **24 of 24 checks** (headless Chrome with real mouse and keyboard input; sign-in, guest showcase, paste-URL import, isolation between users, cancellation). Run locally against a **fake GitHub**; its GitHub Actions run has not happened yet. |
| Workflow files | pass `actionlint` |

The backend suite uses real PostgreSQL and real Redis (the limiter's atomicity is tested with 120 concurrent reservations against a limit of 20). It creates and drops its own `repolens_test` database, is isolated by environment variables set before the settings are read (test database, Redis database 15 with its own queue name, blank provider keys) and refuses to start against a non-local server or a Redis or test database that holds data it did not create; a run was observed with zero connections to the development database. It makes no real network calls.

**Real GitHub OAuth is not verified.** No OAuth App credentials are configured, so no real GitHub login has been performed. Everything above used a mocked identity or a fake GitHub that enforces PKCE. The setup is in the README; the first real login should be watched.

Browser behaviour for Ask AI, citation previews, the Architecture Explorer and error recovery, and live Groq and Gemini calls, were verified in Milestone 1 ([verification-m1.md](verification-m1.md)); that run predates authentication and is not repeated by the smoke test.

## Continuous integration

Three workflows in `.github/workflows/`, all triggered by pushes to `main` and pull requests targeting `main`, with `contents: read` permission only, one run per ref at a time, and a 20 minute limit.

**Backend CI** (`backend-ci.yml`): Python 3.14 with pip caching; service containers for pgvector PostgreSQL and Redis with throwaway credentials; install `requirements-dev.txt`; fetch and cache the tree-sitter grammars; `alembic upgrade head`; `alembic check`; migration downgrade and upgrade round trip; `pytest`; start the API and check `/health`, `/health/ready` and `/repositories`.

**Browser smoke test** (`e2e-smoke.yml`): the same services, then `python -m tests.e2e.run_smoke`, which boots an isolated stack (own database, Redis database 9, unique queue, fake GitHub) and runs the 24 browser checks in headless Chrome.

**Frontend CI** (`frontend-ci.yml`): Node 22 with npm caching; `npm ci`; `npm test`; `npm run typecheck`; `npm run lint -- --max-warnings 0`; `npm run build` with a placeholder `NEXT_PUBLIC_API_URL`.

No workflow sets a Gemini, Groq or GitHub credential. The only network access beyond installing packages is the one-time grammar download.

**Remote status.** Backend CI and Frontend CI passed on GitHub for the Phase 1 commits. The Phase 2 commits have not been pushed, so none of the three workflows has run on them; the backend and frontend jobs were rehearsed on clean Linux, the smoke test only on macOS (it needs Chrome).

## Local start-up

See the README for the full procedure. In short, from the repository root:

```bash
cp .env.example .env && docker compose up -d
python3 -m venv apps/api/venv
apps/api/venv/bin/python -m pip install -r apps/api/requirements-dev.txt
apps/api/venv/bin/alembic upgrade head
apps/api/venv/bin/python -c "from apps.api.services.code_parser import ensure_grammars; ensure_grammars()"
apps/api/venv/bin/uvicorn apps.api.main:app --reload --port 8765     # terminal 1
apps/api/venv/bin/python -m apps.api.worker                           # terminal 2
cd apps/web && npm ci && npm run dev                                 # terminal 3
```

## Environment variables

None of these hold secrets in the repository. Real values go in the git-ignored `.env`.

| Variable | Required | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | yes | Async PostgreSQL URL (`postgresql+asyncpg://...`). |
| `REDIS_URL` | yes | Redis URL for the job queue and readiness check. |
| `WEB_ORIGINS` | yes | Comma-separated browser origins allowed by CORS. |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_PORT`, `REDIS_PORT` | for Docker Compose | Local service configuration. |
| `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET` | for sign-in | The GitHub OAuth App's credentials (secret: `.env` or the platform's secret store only). Without them nobody can sign in or import. |
| `WEB_BASE_URL`, `API_PUBLIC_URL` | yes | Where the browser returns after sign-in, and the API's own public URL (the OAuth callback is `<API_PUBLIC_URL>/auth/github/callback`). Must be same-site. |
| `ENVIRONMENT` | production | `production` turns on Secure `__Host-` cookies, HSTS and CSP, hides the API docs, and makes the API refuse an unsafe configuration at start-up. |
| `GUEST_SHOWCASE_REPOSITORIES` | optional | `owner/name` list visitors may browse read-only. Empty means nothing is public. |
| `TRUSTED_PROXIES` | behind a proxy | Addresses of reverse proxies whose `X-Forwarded-For` is believed. |
| `AI_*`, `IMPORT_*`, `MAX_REPOSITORIES_PER_USER`, `EMBEDDING_BUILD_HOURLY_LIMIT`, `ARCHITECTURE_HOURLY_LIMIT`, `GUEST_READS_PER_MINUTE`, `AUTH_LOGIN_PER_MINUTE` | optional | Usage limits; defaults and meaning in `.env.example`. |
| `GEMINI_API_KEY` | optional | Enables semantic search and Gemini answers. Without it, search is keyword-only, and Ask AI returns HTTP 503 unless `GENERATION_PROVIDER=groq` with a Groq key. |
| `GENERATION_PROVIDER` | optional | `gemini` (default) or `groq`. There is no automatic fallback. |
| `GROQ_API_KEY` | optional | Needed only when `GENERATION_PROVIDER=groq`. |
| `GITHUB_TOKEN` | optional | Raises the GitHub rate limit from 60 to 5000 requests per hour. |
| `EMBEDDING_MODEL`, `GEMINI_GENERATION_MODEL`, `GROQ_GENERATION_MODEL`, `ARCHITECTURE_MAX_FILES` | optional | Model names and the architecture file cap. |
| `NEXT_PUBLIC_API_URL` | optional (web) | API base URL, inlined into the browser bundle at build time. Defaults to `http://localhost:8765`. Never put a secret here. |

About 45 further tuning settings (timeouts, size limits, retrieval and retry parameters) have defaults in `apps/api/config.py`.

## Known limitations

- **Real GitHub sign-in is untested.** See above. Until an OAuth App is configured, only visitors and tests can use the app.
- **Public repositories only.** There is no private repository support and no GitHub token is retained.
- **Quotas are per account.** Anyone can create many GitHub accounts; the global daily AI budget and concurrency limit bound the total cost, but there is no CAPTCHA, email check or account-age rule. See [security.md](security.md).
- **No account deletion or data export** flow yet (database deletes cascade).
- **Shared repository snapshots.** A re-ingestion by any member refreshes the snapshot for everyone who added that repository.
- **Not deployed** at the time of this record. The hosting that followed (Vercel + Render + Neon + Redis, same-site through a rewrite) is described in [deployment.md](deployment.md); the operator checklist is in [security.md](security.md).
- **Web security headers** (CSP, frame ancestors) for the Next.js app are not configured; the API sends its own.
- **Manual grammar step.** Tree-sitter grammars must be downloaded once with `ensure_grammars()`. The worker only warns if they are missing, and without them files are indexed as text and architecture analysis cannot read imports.
- **Language coverage.** Structure-aware chunking and the import graph cover Python, JavaScript, TypeScript and TSX only. Dynamic imports and runtime wiring are not resolved. Architecture areas and technologies are heuristic and marked "Inferred".
- **AI answers can be wrong.** Citations are checked against what was retrieved, but the claims in the text are not verified. Semantic retrieval needs a Gemini key and is subject to its quotas; otherwise keyword matching is used.
- **Chat details.** An answer still streaming is lost if you leave the Ask AI tab. Conversation titles saved by earlier versions are not rewritten. A conversation is only created when the first question is sent.
- **Graph layout.** A dense, fully cyclic dependency graph is laid out as one long chain that is tiny at "fit to view". Zoom, find and the details panel still work.
- **Keyword-only retrieval.** Without embeddings, conceptual questions can miss the relevant code; exact identifiers work well.
- **Test coverage.** The frontend unit tests cover pure logic (graph model, citations, titles, statistics, sign-in helpers). Browser automation is limited to the 24-check smoke test; Ask AI, the architecture graph and error recovery were verified by hand in Milestone 1 and are not automated.
- **Housekeeping.** `cmd.txt` and `step.txt` at the repository root are personal development notes that are tracked in Git. `agent/`, `mcp/`, `ingestion/`, `packages/` and `infrastructure/` contain only `.gitkeep` placeholders.

## What remains before deployment

1. Push the Phase 2 commits and confirm all three GitHub Actions workflows pass.
2. Register a GitHub OAuth App (development first), perform and watch a real sign-in, then repeat with a production OAuth App.
3. Choose a deployment target for the API, the worker, PostgreSQL with pgvector, Redis and the web app on one registrable domain; follow the operator checklist in [security.md](security.md) (HTTPS, secrets, `ENVIRONMENT=production`, `TRUSTED_PROXIES`, backups, migrations on release, grammars fetched at image build time).
4. Import and index the showcase repositories, then list them in `GUEST_SHOWCASE_REPOSITORIES`.
5. Set provider-side spend caps next to `AI_GLOBAL_DAILY_LIMIT`, and monitor 429/401/403 rates and queue depth.
6. Optional hardening later: CAPTCHA or account-age checks, account deletion, web-app security headers, automated browser tests of Ask AI and the graph.

Out of scope for this baseline, and not started: LangGraph, MCP, and autonomous code editing.
