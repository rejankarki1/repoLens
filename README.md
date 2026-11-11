# repoLens

repoLens is an AI-powered repository intelligence tool for public GitHub repositories. It ingests a repository, makes its code searchable, lets you ask questions that are answered from the code with citations to the exact files and lines, and maps how the modules depend on each other.

> **Hosting:** Vercel (web app), Render (API and worker), Neon (PostgreSQL), Redis for the job queue. See [docs/deployment.md](docs/deployment.md) for the production steps and [docs/release-baseline.md](docs/release-baseline.md) for what was verified.

## What it does

- **Sign in with GitHub.** Identity only: no repository access is requested. Each user has a private workspace and private conversations; repository data (files, embeddings, architecture) is shared between users who analyze the same public repository.
- **Guest demo.** The home page (`/`) is a public landing page; signed-in users go straight to their dashboard. Visitors can browse an explicit allow-list of pre-indexed repositories read-only (overview, source, architecture) and read curated example answers with their citations; they cannot import, re-analyze, delete, or search. They can ask a few live questions about the showcase repositories when the operator enables `GUEST_AI_ENABLED` (capped per visitor and for all guests, nothing saved). The example answers are real saved answers published with `apps/api/scripts/publish_showcase_answers.py` (see [docs/deployment.md](docs/deployment.md)), so visitors never use the AI budget.
- **Ingestion.** Paste a public GitHub repository URL (signing in if needed, and your URL is kept through sign-in); a Redis/RQ worker downloads and indexes it in the background.
- **Repository management.** The Repositories page searches your workspace and re-analyzes or removes repositories. Removing one deletes your membership and your conversations about it; the stored copy is deleted only if nobody else has added it and it is not in the showcase. Your GitHub repository is never touched.
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

2. Create a GitHub OAuth App for sign-in (see [GitHub OAuth App setup](#github-oauth-app-setup)), copy `.env.oauth-check.example` to `.env.oauth-check`, `chmod 600` it, and put the client id and secret there (see [Start and stop](#start-and-stop)). Without them the app still starts and visitors can browse the showcase, but nobody can sign in or import.

3. Install API dependencies, apply migrations, and fetch the tree-sitter grammars:

   ```bash
   python3 -m venv apps/api/venv
   apps/api/venv/bin/python -m pip install -r apps/api/requirements-dev.txt
   apps/api/venv/bin/alembic upgrade head
   apps/api/venv/bin/python -c "from apps.api.services.code_parser import ensure_grammars; ensure_grammars()"
   ```

   The grammar download needs the network and is done once; the parser never downloads grammars while it runs. Without it, files are indexed as plain text and architecture analysis cannot read imports. Use `requirements.txt` instead of `requirements-dev.txt` if you do not need the test tools.

   **Upgrading a database that predates sign-in?** Read [docs/auth-migration.md](docs/auth-migration.md) first: your old repositories and conversations are kept, and you take them over explicitly with `python -m apps.api.scripts.claim_local_data`.

4. Install the web app's dependencies once: `cd apps/web && npm ci`.

## Start and stop

Run each service in its own terminal, from the repository root (the web app from `apps/web`):

```bash
docker compose up -d                                              # PostgreSQL + Redis
apps/api/venv/bin/uvicorn apps.api.main:app --reload --port 8765  # FastAPI
apps/api/venv/bin/python -m apps.api.worker                       # RQ worker
cd apps/web && npm run dev                                        # Next.js
```

Open `http://localhost:3000` (use `localhost`, not `127.0.0.1`: the session cookie needs the web app and the API on the same site). `http://localhost:8765/docs` is the API documentation and `/health/ready` reports PostgreSQL and Redis. Stop a process with Ctrl-C; stop the containers with `docker compose stop` (never `docker compose down -v`, which deletes the data).

**Configuration: two files, read automatically.** The API, the worker and Alembic all read `apps/api/config.py`, which loads `.env` and then `.env.oauth-check` (the later file wins; a file that does not exist is skipped). Nothing needs to be exported or sourced.

| File | Holds | Git |
| --- | --- | --- |
| `.env` | the shared base: Docker ports, `GEMINI_API_KEY`, `GROQ_API_KEY`, `GENERATION_PROVIDER`, models, and the default (development) `DATABASE_URL` / `REDIS_URL` | ignored |
| `.env.oauth-check` | only what differs for the isolated OAuth-check setup: `DATABASE_URL` (`repolens_oauth_check`), `REDIS_URL` (database 8), `INGESTION_QUEUE_NAME=oauth_check`, the GitHub OAuth App id and secret, and the `localhost` URLs | ignored (template: `.env.oauth-check.example`) |

So while `.env.oauth-check` exists, every plain command uses the isolated database, Redis database 8 and the `oauth_check` queue, and the AI keys come from `.env`. To work against your real development resources, move `.env.oauth-check` aside. Put AI keys in `.env` only. A variable exported in your shell still beats both files, so if the wrong database or queue appears, check `env | grep -E 'DATABASE_URL|REDIS_URL|INGESTION|GEMINI|GROQ|GITHUB'` and `unset` the stray ones.

Do not use the `oauth-check` overlay in production: set the real values through the production secret store (see `docs/security.md`).

## Health check and troubleshooting

```bash
curl -s localhost:8765/health/ready     # {"status":"ready","checks":{"database":"ok","redis":"ok"}}
docker compose ps                        # postgres and redis should be "running"
```

| Symptom | Fix |
| --- | --- |
| `ConnectionRefusedError` on `127.0.0.1:5433` or `6379` | Docker is not running or the containers are stopped: start Docker Desktop, then `docker compose up -d`. If `docker` itself hangs, quit and reopen Docker Desktop. |
| `port is already allocated` | Another service uses 5433, 6379, 8765 or 3000. Stop it, or change the port in `.env` (and `DATABASE_URL` / `REDIS_URL` with it). |
| `No script_location` from Alembic | Run it from the repository root (`alembic.ini` has a relative path), as `apps/api/venv/bin/alembic upgrade head`. |
| `password authentication failed` | `DATABASE_URL` does not match `POSTGRES_USER` / `POSTGRES_PASSWORD` in `.env`. A volume created with an older password keeps it; change `.env` back, or recreate the volume only if you accept losing local data. |
| Jobs stay "queued" | The worker is not running, or it listens on another queue: `apps/api/venv/bin/python -m apps.api.worker`, and check `INGESTION_QUEUE_NAME` matches the API. |
| Sign-in loops or the session is lost | Open the app at `http://localhost:3000`, not `127.0.0.1`. |
| Indexing stuck at "embedding" | Use **Resume Indexing** in the chat panel. Gemini's free tier rate-limits embeddings; it continues where it stopped. |

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
5. Put the values in `.env.oauth-check` locally (the app reads it after `.env`), or in the production secret store when deployed. Never commit them, and never put them in `apps/web`: `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET`, `API_PUBLIC_URL`, `WEB_BASE_URL`, `WEB_ORIGINS`. Use a separate OAuth App for production, with the production URLs.
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
