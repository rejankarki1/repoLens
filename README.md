# repoLens

repoLens is a planned developer assistant for exploring GitHub repositories. It will ingest source code, make it searchable with retrieval-augmented generation (RAG), and use an agent to answer questions about the codebase.

> **Project status:** The Next.js starter app and FastAPI service exist. Phase 2 adds local PostgreSQL, pgvector, Redis, and foundation migrations. Repository ingestion and the agent are still planned.

## Planned capabilities

- Parse and index repository files.
- Search code by meaning with vector embeddings.
- Explain code, dependencies, and repository structure with references to relevant files.
- Assist with debugging and code review questions.

## Planned technology

| Area | Technology |
| --- | --- |
| Web app | Next.js, React, Tailwind CSS |
| API | Python, FastAPI |
| Agent workflow | LangGraph |
| Database and vector search | PostgreSQL, pgvector |
| Local services | Docker Compose |

The web and API dependencies are defined in their respective application folders.

## How it is intended to work

1. **Ingestion** reads repository files, parses them, and splits large files into useful chunks.
2. **Embeddings and indexing** turn chunks into searchable records in the database.
3. **The API** receives a question from the web app.
4. **The agent** searches the index, uses the relevant code as context, and returns an answer.

## Repository structure

```text
repoLens/
├── agent/
│   ├── agents/          # Individual agent roles
│   ├── orchestrator/    # Agent workflow and coordination
│   ├── prompts/         # Agent instructions
│   ├── state/           # Workflow state definitions
│   └── tools/           # Actions available to agents
├── apps/
│   ├── api/             # Planned FastAPI service
│   └── web/             # Planned Next.js app
├── database/            # Alembic migrations
├── docs/                # Project documentation
├── infrastructure/      # Deployment and service configuration
├── ingestion/
│   ├── parsers/         # Extract content from repository files
│   ├── chunking/        # Split content into searchable pieces
│   ├── embeddings/      # Generate vector representations
│   └── indexing/        # Store and update searchable records
├── mcp/                 # Planned Model Context Protocol integrations
├── packages/            # Shared code
├── tests/               # Automated tests
├── .env.example         # Local development settings template
├── .gitignore           # Local files excluded from Git
├── docker-compose.yml   # PostgreSQL with pgvector and Redis
├── alembic.ini          # Migration configuration
└── README.md
```

Empty directories may contain `.gitkeep` files so Git can retain them.

## Local setup

Run these commands from the repository root. You need Python 3, Node.js, and Docker Desktop (or another running Docker daemon).

1. Copy the local settings and start the database services:

   ```bash
   cp .env.example .env
   docker compose up -d
   docker compose ps
   ```

   The example password is only for local development. If you change `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, or the exposed ports in `.env`, update `DATABASE_URL` or `REDIS_URL` in the same file to match. Do not commit `.env`.

2. Install API dependencies and apply migrations:

   ```bash
   python3 -m venv apps/api/venv
   apps/api/venv/bin/python -m pip install -r apps/api/requirements.txt
   apps/api/venv/bin/alembic upgrade head
   ```

   The first migration creates users, GitHub accounts, repositories, conversations, and messages. The second enables the `vector` extension; code chunk and embedding tables come later.

3. Start the API:

   ```bash
   apps/api/venv/bin/uvicorn apps.api.main:app --reload --port 8765
   ```

   Open `http://127.0.0.1:8765/docs`. `/health` checks that the API responds; `/health/ready` checks PostgreSQL and Redis and returns HTTP 503 if either is unavailable. The API uses port 8765 in these instructions because port 8000 is already in use on this development machine. PostgreSQL is exposed on port 5433 because a local PostgreSQL server already uses 5432.

4. In a second terminal, start the web app:

   ```bash
   cd apps/web
   npm install
   npm run dev
   ```

   Open `http://localhost:3000`. The frontend is still the generated Next.js starter page.

To stop local services without deleting their named volumes, run `docker compose down`. The Compose ports bind to `127.0.0.1` for local access only. Redis currently has no application feature using it; its client and readiness check are prepared for caching or session state.
