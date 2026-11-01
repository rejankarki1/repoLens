# repoLens

repoLens is an AI-powered repository intelligence tool for public GitHub repositories. It ingests a repository, makes its code searchable, lets you ask questions that are answered from the code with citations to the exact files and lines, and maps how the modules depend on each other.

> **Project status:** a working application with GitHub sign-in, per-user workspaces and usage limits. It is not deployed. Real GitHub OAuth has not yet been exercised end to end (tests use a fake GitHub), so treat it as unverified until you have configured an OAuth App. See [docs/release-baseline.md](docs/release-baseline.md) for what exists, what was verified, and what remains.

## What it does

- **Sign in with GitHub.** Identity only: no repository access is requested. Each user has a private workspace and private conversations; repository data (files, embeddings, architecture) is shared between users who analyze the same public repository.
- **Guest showcase.** Visitors can browse an explicit allow-list of pre-indexed repositories read-only (overview, source, architecture); they cannot import, search, or ask questions.
- **Ingestion.** Paste a public GitHub repository URL (signing in if needed, and your URL is kept through sign-in); a Redis/RQ worker downloads and indexes it in the background.
- **Code Explorer.** Browse the file tree and source, with a dependency panel for each file.
- **Ask AI.** Questions are answered by Gemini or Groq from retrieved code only, with clickable citations that open a source preview. Conversations are saved and can be renamed, deleted, or cleared.
- **Hybrid retrieval.** PostgreSQL full-text search plus pgvector similarity over tree-sitter code chunks, with Gemini embeddings (optional; keyword search works without a key).
- **Architecture Explorer.** A deterministic, evidence-linked dependency graph of modules and files. It needs no AI key.

## Technology

| Area | Technology |
| --- | --- |
| Web app | Next.js 16, React, Tailwind CSS, SWR, React Flow |
| API | Python, FastAPI, SQLAlchemy (async), Alembic |
| Background jobs | Redis, RQ |
| Code parsing | tree-sitter (`tree-sitter-language-pack`) |
| Database and vector search | PostgreSQL, pgvector |
| AI providers | Gemini (embeddings and answers), Groq (answers, optional) |
| Local services | Docker Compose |

## How it works

1. **Ingestion** downloads the repository archive, stores its files, and splits code into chunks with tree-sitter.
2. **Embeddings and indexing** store chunks for keyword search and, when `GEMINI_API_KEY` is set, vector search.
3. **Ask AI** retrieves the most relevant chunks, asks the selected provider to answer using only those, and validates every citation against what was retrieved ([docs/rag.md](docs/rag.md)).
4. **Architecture analysis** reads imports with tree-sitter and builds the dependency graph ([docs/architecture.md](docs/architecture.md)).

More detail: [docs/search.md](docs/search.md), [docs/erd.md](docs/erd.md).

## Repository structure

```text
repoLens/
├── agent/               # Placeholder for future agent workflows (not implemented)
├── apps/
│   ├── api/             # FastAPI service, RQ worker, services and jobs
│   └── web/             # Next.js app
├── database/            # Alembic migrations
├── docs/                # Project documentation
├── infrastructure/      # Placeholder for deployment configuration
├── ingestion/           # Placeholder directories; the ingestion code lives in apps/api/services and apps/api/jobs
├── mcp/                 # Placeholder (not implemented)
├── packages/            # Placeholder for shared code
├── tests/               # Automated tests
├── .env.example         # Local development settings template
├── .github/workflows/   # Backend and frontend CI
├── .gitignore           # Local files excluded from Git
├── docker-compose.yml   # PostgreSQL with pgvector and Redis
├── alembic.ini          # Migration configuration
└── README.md
```

Empty directories may contain `.gitkeep` files so Git can retain them. The placeholder directories contain nothing but `.gitkeep`.

## Local setup

Run these commands from the repository root. You need Python 3.14, Node.js 22.18 or newer, and Docker Desktop (or another running Docker daemon).

1. Copy the local settings and start PostgreSQL and Redis:

   ```bash
   cp .env.example .env
   docker compose up -d
   docker compose ps
   ```

   The example password is only for local development. If you change `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, or the exposed ports in `.env`, update `DATABASE_URL` or `REDIS_URL` in the same file to match. Do not commit `.env`. API keys (`GEMINI_API_KEY`, `GROQ_API_KEY`, `GITHUB_TOKEN`) are optional and belong only in `.env`.

2. Create a GitHub OAuth App for sign-in (see [GitHub OAuth App setup](#github-oauth-app-setup)) and put its client id and secret in `.env`. Without them the app still starts and visitors can browse the showcase, but nobody can sign in or import.

3. Install API dependencies, apply migrations, and fetch the tree-sitter grammars:

   ```bash
   python3 -m venv apps/api/venv
   apps/api/venv/bin/python -m pip install -r apps/api/requirements-dev.txt
   apps/api/venv/bin/alembic upgrade head
   apps/api/venv/bin/python -c "from apps.api.services.code_parser import ensure_grammars; ensure_grammars()"
   ```

   The grammar download needs the network and is done once; the parser never downloads grammars while it runs. Without it, files are indexed as plain text and architecture analysis cannot read imports. Use `requirements.txt` instead of `requirements-dev.txt` if you do not need the test tools.

4. Start the API:

   ```bash
   apps/api/venv/bin/uvicorn apps.api.main:app --reload --port 8765
   ```

   Open `http://localhost:8765/docs`. `/health` checks that the API responds; `/health/ready` checks PostgreSQL and Redis and returns HTTP 503 if either is unavailable. PostgreSQL is exposed on port 5433 in `.env.example` because a local PostgreSQL server may already use 5432.

5. In a second terminal, start the background worker. Importing a repository needs it:

   ```bash
   apps/api/venv/bin/python -m apps.api.worker
   ```

6. In a third terminal, start the web app:

   ```bash
   cd apps/web
   cp .env.example .env.local   # optional: the API URL defaults to http://localhost:8765
   npm ci
   npm run dev
   ```

   Open `http://localhost:3000` (use `localhost`, not `127.0.0.1`: the session cookie needs the web app and the API on the same site). Paste a public GitHub repository URL, continue with GitHub, and wait for it to finish ingesting.

   **Upgrading a database that predates sign-in?** Read [docs/auth-migration.md](docs/auth-migration.md) first: your old repositories and conversations are kept, and you take them over explicitly with `python -m apps.api.scripts.claim_local_data`.

To stop local services without deleting their named volumes, run `docker compose down`. The Compose ports bind to `127.0.0.1` for local access only.

## Tests

```bash
apps/api/venv/bin/python -m pytest -q           # backend; needs PostgreSQL with pgvector, creates a disposable repolens_test database
cd apps/web && npm test && npm run typecheck && npm run lint && npm run build
```

**Test isolation.** The tests can never reach your development data: before anything reads the settings, `tests/conftest.py` points `DATABASE_URL` at `repolens_test`, `REDIS_URL` at database 15 with its own queue name, and blanks the Gemini, Groq and GitHub secrets, so every reader of the settings (including job entry points and Alembic) lands in disposable resources. The suite refuses to start if the database or Redis host is not local (override with `REPOLENS_TEST_ALLOW_REMOTE=1` for a disposable CI service), if Redis database 15 holds data it did not create, or if `repolens_test` holds application data. The smoke test applies the same ownership rules to its database (`repolens_e2e`) and Redis database 9.

A browser smoke test (headless Chrome, a fake GitHub, its own database, Redis database and ports) covers sign-in, the guest showcase, the paste-URL flow and isolation between users:

```bash
python -m tests.e2e.run_smoke      # needs Chrome (or CHROME_BIN) and the web app's dependencies; never touches development data
```

The same checks run in GitHub Actions on every push and pull request to `main` (`.github/workflows/`). They use throwaway services and no AI, OAuth or GitHub credentials.

## GitHub OAuth App setup

RepoLens signs users in with a GitHub OAuth App. It asks for no scopes, so the token can read only public profile data (id, username, name, avatar); it is used once, revoked, and never stored.

1. GitHub → Settings → Developer settings → OAuth Apps → **New OAuth App**.
2. **Homepage URL**: your web app (`http://localhost:3000` locally).
3. **Authorization callback URL**: the API's callback, exactly `<API_PUBLIC_URL>/auth/github/callback` (`http://localhost:8765/auth/github/callback` locally). GitHub only returns the browser to this URL.
4. Create the app, then **Generate a new client secret**.
5. Put the values in `.env` (never commit them, and never put them in `apps/web`): `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET`, `API_PUBLIC_URL`, `WEB_BASE_URL`, `WEB_ORIGINS`. Use a separate OAuth App for production, with the production URLs.
6. Restart the API. `GET /auth/me` now reports `github_login_enabled: true` and "Continue with GitHub" works.

To let visitors browse some repositories without signing in, import them first, then list them in `GUEST_SHOWCASE_REPOSITORIES` (`owner/name`, comma separated). Nothing is public unless it is listed.

## Who can do what

| | Visitor (guest) | Signed in |
| --- | --- | --- |
| Showcase repositories: overview, source, architecture | read-only | read-only until added to your repositories |
| Your own repositories | none | full use |
| Paste a URL and analyze a public repository | asked to sign in; the URL is kept | yes (per-hour and concurrent import limits) |
| Ask AI, conversations, search, re-analysis | no | yes, on repositories in your workspace (per-day, burst and shared-budget limits) |
| Someone else's conversations or repositories | no | no |

Limits are settings (`AI_*`, `IMPORT_*`, `GUEST_*`, `AUTH_*` in `.env.example`) enforced server-side in Redis. See [docs/auth-design.md](docs/auth-design.md) and [docs/security.md](docs/security.md).
