# repoLens

repoLens is an AI-powered repository intelligence tool for public GitHub repositories. It ingests a repository, makes its code searchable, lets you ask questions that are answered from the code with citations to the exact files and lines, and maps how the modules depend on each other.

> **Project status:** a working local application. It is not deployed, has no authentication, and is not production-ready. See [docs/release-baseline.md](docs/release-baseline.md) for what exists, what was verified, and what remains.

## What it does

- **Ingestion.** Import a public GitHub repository by URL; a Redis/RQ worker downloads and indexes it in the background.
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

2. Install API dependencies, apply migrations, and fetch the tree-sitter grammars:

   ```bash
   python3 -m venv apps/api/venv
   apps/api/venv/bin/python -m pip install -r apps/api/requirements-dev.txt
   apps/api/venv/bin/alembic upgrade head
   apps/api/venv/bin/python -c "from apps.api.services.code_parser import ensure_grammars; ensure_grammars()"
   ```

   The grammar download needs the network and is done once; the parser never downloads grammars while it runs. Without it, files are indexed as plain text and architecture analysis cannot read imports. Use `requirements.txt` instead of `requirements-dev.txt` if you do not need the test tools.

3. Start the API:

   ```bash
   apps/api/venv/bin/uvicorn apps.api.main:app --reload --port 8765
   ```

   Open `http://127.0.0.1:8765/docs`. `/health` checks that the API responds; `/health/ready` checks PostgreSQL and Redis and returns HTTP 503 if either is unavailable. PostgreSQL is exposed on port 5433 in `.env.example` because a local PostgreSQL server may already use 5432.

4. In a second terminal, start the background worker. Importing a repository needs it:

   ```bash
   apps/api/venv/bin/python -m apps.api.worker
   ```

5. In a third terminal, start the web app:

   ```bash
   cd apps/web
   cp .env.example .env.local   # optional: the API URL defaults to http://127.0.0.1:8765
   npm ci
   npm run dev
   ```

   Open `http://localhost:3000`, paste a public GitHub repository URL, and wait for it to finish ingesting.

To stop local services without deleting their named volumes, run `docker compose down`. The Compose ports bind to `127.0.0.1` for local access only.

## Tests

```bash
apps/api/venv/bin/python -m pytest -q           # backend; needs PostgreSQL with pgvector, creates a disposable repolens_test database
cd apps/web && npm test && npm run typecheck && npm run lint && npm run build
```

The same checks run in GitHub Actions on every push and pull request to `main` (`.github/workflows/`). They use throwaway services and no AI or GitHub credentials.
