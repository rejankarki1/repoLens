# RepoLens release baseline

A record of what RepoLens is, how it was verified, and what is left, taken before authentication and deployment work begins. It describes a **working local application**. It is not deployed and it is not production-ready.

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
| Architecture Explorer | A deterministic, evidence-linked dependency graph of modules and files with filters, search, and drill-down. Every edge links to the import statement behind it. Technologies and architecture areas are inferred and labelled as such. No AI provider is involved. |
| Overview | Real file, directory, line and language statistics and a summary of the stored architecture analysis. |

Detail: [rag.md](rag.md), [search.md](search.md), [architecture.md](architecture.md), [erd.md](erd.md).

### Stack

FastAPI, SQLAlchemy (async), Alembic, PostgreSQL 17 with pgvector 0.8.7, Redis 7.4, RQ. Next.js 16, React 19, Tailwind 4, SWR, React Flow. Python 3.14, Node 22.18 or newer.

## Verified results

Run on 2026-10-31 against the repository as committed plus the CI, README and documentation changes of this milestone, in two ways: on the development machine, and on a clean Linux container with no `.env`, a freshly created pgvector database and Redis, and dependencies installed from the repository's own requirements and lockfile.

| Check | Result |
| --- | --- |
| Backend tests (`pytest`) | **604 passed**, 2 warnings (third-party deprecation notices). Also 604 passed on clean Linux, and with all outbound HTTP(S) forced to a dead proxy, which shows the tests make no real network calls. |
| Frontend unit tests (`npm test`) | **44 passed**, 0 failed |
| TypeScript (`tsc --noEmit`) | clean |
| ESLint (`--max-warnings 0`) | 0 errors, 0 warnings |
| Next.js production build | succeeds |
| Alembic | `upgrade head` on an empty database applies all 8 migrations, `check` reports no drift between models and migrations, and `downgrade base` followed by `upgrade head` round-trips |
| API start-up | `/health` returns `ok`, `/health/ready` reports database and Redis `ok`, `/repositories` returns 200 |
| Workflow files | pass `actionlint` |

The backend suite creates and drops its own `repolens_test` database on the server named by `DATABASE_URL`; it never touches the `repolens` database.

Browser behaviour (citation preview, conversation management, graph filters, mobile layouts) was verified by hand in headless Chrome during the UI/UX milestone. That is not automated and is not part of CI.

### Findings from the clean-machine run

- Three architecture test files assume the tree-sitter grammars are already downloaded, and other test files download them lazily. On a clean machine, 29 tests failed until the grammars were fetched first. CI now has an explicit provisioning step that fails if any grammar is missing. No test or application code was changed.
- The README was out of date (it described ingestion and the agent as planned). It now matches the application.

## Continuous integration

Two workflows in `.github/workflows/`, both triggered by pushes to `main` and pull requests targeting `main`, with `contents: read` permission only, one run per ref at a time, and a 20 minute limit.

**Backend CI** (`backend-ci.yml`): Python 3.14 with pip caching; service containers for pgvector PostgreSQL and Redis with throwaway credentials; install `requirements-dev.txt`; fetch and cache the tree-sitter grammars; `alembic upgrade head`; `alembic check`; migration downgrade and upgrade round trip; `pytest`; start the API and check `/health`, `/health/ready` and `/repositories`.

**Frontend CI** (`frontend-ci.yml`): Node 22 with npm caching; `npm ci`; `npm test`; `npm run typecheck`; `npm run lint -- --max-warnings 0`; `npm run build` with a placeholder `NEXT_PUBLIC_API_URL`.

No workflow sets a Gemini, Groq or GitHub credential. The only network access beyond installing packages is the one-time grammar download.

**Remote status: not yet verified.** The workflows have been linted and every step was rehearsed locally on clean Linux, but they have not run on GitHub. This baseline is complete only once both workflows pass there.

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
| `GEMINI_API_KEY` | optional | Enables semantic search and Gemini answers. Without it, search is keyword-only, and Ask AI returns HTTP 503 unless `GENERATION_PROVIDER=groq` with a Groq key. |
| `GENERATION_PROVIDER` | optional | `gemini` (default) or `groq`. There is no automatic fallback. |
| `GROQ_API_KEY` | optional | Needed only when `GENERATION_PROVIDER=groq`. |
| `GITHUB_TOKEN` | optional | Raises the GitHub rate limit from 60 to 5000 requests per hour. |
| `EMBEDDING_MODEL`, `GEMINI_GENERATION_MODEL`, `GROQ_GENERATION_MODEL`, `ARCHITECTURE_MAX_FILES` | optional | Model names and the architecture file cap. |
| `NEXT_PUBLIC_API_URL` | optional (web) | API base URL, inlined into the browser bundle at build time. Defaults to `http://127.0.0.1:8765`. Never put a secret here. |

About 45 further tuning settings (timeouts, size limits, retrieval and retry parameters) have defaults in `apps/api/config.py`.

## Known limitations

- **No authentication.** Every conversation belongs to one local default user, and the interface shows a placeholder "Alex Doe / Demo workspace" persona and a DEMO badge. Anyone who can reach the API can read and delete everything.
- **No repository access model.** Only public GitHub repositories can be imported; there is no GitHub sign-in and no private repository support.
- **Not deployed.** There is no production configuration, HTTPS, hosting, secrets management, backup, monitoring, or per-user quota on ingestion size or AI cost.
- **Manual grammar step.** Tree-sitter grammars must be downloaded once with `ensure_grammars()`. The worker only warns if they are missing, and without them files are indexed as text and architecture analysis cannot read imports.
- **Language coverage.** Structure-aware chunking and the import graph cover Python, JavaScript, TypeScript and TSX only. Dynamic imports and runtime wiring are not resolved. Architecture areas and technologies are heuristic and marked "Inferred".
- **AI answers can be wrong.** Citations are checked against what was retrieved, but the claims in the text are not verified. Semantic retrieval needs a Gemini key and is subject to its quotas; otherwise keyword matching is used.
- **Chat details.** An answer still streaming is lost if you leave the Ask AI tab. Conversation titles saved by earlier versions are not rewritten. A conversation is only created when the first question is sent.
- **Test coverage.** The frontend tests cover pure logic (graph model, citations, titles, statistics). There are no automated component or end-to-end browser tests.
- **Housekeeping.** `cmd.txt` and `step.txt` at the repository root are personal development notes that are tracked in Git. `agent/`, `mcp/`, `ingestion/`, `packages/` and `infrastructure/` contain only `.gitkeep` placeholders.

## What remains before deployment

1. Confirm both GitHub Actions workflows pass on GitHub.
2. Authentication and ownership (Phase 8): replace the default-user lookup (`find_default_user_id`), add sign-in, scope repositories and conversations per user, and remove the placeholder persona.
3. Abuse and cost controls: per-user limits on imports, repository size, and AI requests.
4. A deployment target for the API, the worker, PostgreSQL with pgvector, Redis and the web app, with migrations on release and grammars fetched at image build time.
5. Production configuration: secrets management, allowed origins, HTTPS, logging and monitoring, database backups.
6. A security and privacy review, and automated end-to-end tests of the main flows.

Out of scope for this baseline, and not started: LangGraph, MCP, and autonomous code editing.
