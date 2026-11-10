# Deploying the public demo

Vercel Hobby (web) + Render Free (API **and** one RQ worker in one service) + Neon Free (PostgreSQL + pgvector) + hosted Redis + GitHub OAuth. A low-cost portfolio demo, not a production SaaS. Nothing in this repository creates cloud resources; you do the steps below.

## How the pieces connect

```
browser ──https──> Vercel  https://<app>.vercel.app
                     │  /api/*  is rewritten (proxied) to  https://<svc>.onrender.com/*
                     ▼
                   Render  Docker image: apps.api.supervisor ─┬─ uvicorn apps.api.main:app  (0.0.0.0:$PORT, 1 process)
                                                              └─ python -m apps.api.worker  (the only worker)
                     ├─ Neon  (DATABASE_URL, TLS)        └─ Redis (REDIS_URL, rediss://) = RQ queue, OAuth state, rate limits
```

The browser only ever talks to the Vercel origin (`NEXT_PUBLIC_API_URL=/api`), so the session cookie (`__Host-rl_session`, Secure, HttpOnly, SameSite=Lax) is first-party and CORS is not involved. The GitHub OAuth callback is `API_PUBLIC_URL + /auth/github/callback`, i.e. `https://<app>.vercel.app/api/auth/github/callback`, which the rewrite forwards to Render. CSRF (`X-CSRF-Token` + Origin check) is unchanged; `WEB_ORIGINS` must be the Vercel origin because browsers send that Origin on same-origin POSTs.

## 1. Neon

1. Create a project (pick the region closest to your Render region) and a database.
2. Copy the **direct** connection string (host *without* `-pooler`). asyncpg uses prepared statements, which Neon's PgBouncer pooler breaks in transaction mode.
3. Paste it as-is, e.g. `postgresql://user:pass@ep-xxx.region.aws.neon.tech/neondb?sslmode=require&channel_binding=require`. RepoLens rewrites it to `postgresql+asyncpg://…?ssl=require` and drops `channel_binding` itself.
4. pgvector needs nothing manual: migration `0002_pgvector` runs `CREATE EXTENSION IF NOT EXISTS vector`, which Neon allows for the project owner role.

Free tier is 0.5 GB: that is what `MAX_TOTAL_REPOSITORIES` and `IMPORT_GLOBAL_DAILY_LIMIT` protect.

## 2. Redis

RQ needs a real Redis over TCP (blocking pops, pipelines, `WATCH`, Lua); an HTTP/REST-only Redis API will **not** work. Use a `rediss://` URL (TLS); `redis-py` handles it with no code change.

- **Upstash** (TCP/TLS endpoint, not the REST URL): fine functionally, but the free plan has a monthly command cap and an RQ worker polls constantly (heartbeats, scheduler). Watch the usage dashboard in the first days.
- **Redis Cloud free (30 MB)**: no per-command cap, so the safer default for an always-polling worker.

## 3. GitHub OAuth App (production)

GitHub → Settings → Developer settings → OAuth Apps → New. Use a separate app from your local one. No scopes are requested.

| Field | Value |
| --- | --- |
| Homepage URL | `https://<app>.vercel.app` |
| Authorization callback URL | `https://<app>.vercel.app/api/auth/github/callback` |

Generate a client secret and put it only in Render's environment. If you later add a custom domain, change both URLs, `WEB_BASE_URL`, `WEB_ORIGINS` and `API_PUBLIC_URL` together.

## 4. Initialise the database (once, from your machine, before the first deploy)

Migrations are not run on boot (Render Free has no pre-deploy command, and a web process should not migrate on every restart). Run them against Neon from a shell where the production URL is set **explicitly**; environment variables outrank `.env`, so your local database is never used. Nothing else is touched and no data is deleted.

```bash
cd /path/to/repoLens
DATABASE_URL='postgresql://…neon…?sslmode=require' REDIS_URL='redis://127.0.0.1:6379/15' \
  apps/api/venv/bin/alembic upgrade head
DATABASE_URL='postgresql://…neon…?sslmode=require' apps/api/venv/bin/alembic current   # f1c4a7b9e820 (head)
```

(`REDIS_URL` is only there so nothing can reach your real queue; alembic does not use it.) Do not run `claim_local_data` on production. Local users, sessions and conversations are never copied.

## 5. Render

1. New → Blueprint → select the GitHub repository (it reads `render.yaml`), or New → Web Service → Docker, plan **Free**, health check path `/health`, auto-deploy off.
2. Fill the `sync: false` variables (checklist below). Leave `PORT` alone; Render sets it.
3. Deploy. Build: `docker build` using `./Dockerfile` (installs `apps/api/requirements.txt` only, bakes the tree-sitter grammars). Start: `python -m apps.api.supervisor` (the image `CMD`).

Process model: one uvicorn process + one RQ worker (with scheduler, as the retry backoff needs). Both are started in their own process groups. SIGTERM/SIGINT stops both (worker gets a warm shutdown first, SIGKILL after 25 s). If either exits, the other is stopped and the service exits non-zero so Render restarts it and the failure is visible in the logs.

## 6. Vercel

Import the repository; **Root Directory `apps/web`**; Framework Next.js; default build (`npm run build`) and install commands. Environment variables (Production), **set before the first build** because both are read at build time:

| Variable | Value |
| --- | --- |
| `NEXT_PUBLIC_API_URL` | `/api` |
| `API_ORIGIN` | `https://<service>.onrender.com` (no trailing slash, no `/api`) |

Then set Render's `WEB_BASE_URL`/`WEB_ORIGINS`/`API_PUBLIC_URL` to the final Vercel URL and redeploy Render.

## Environment variable checklist

**Render** (secrets marked ★): `ENVIRONMENT=production`, `DATABASE_URL`★, `REDIS_URL`★, `WEB_BASE_URL`, `WEB_ORIGINS`, `API_PUBLIC_URL`, `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET`★, `GEMINI_API_KEY`★ (embeddings are always Gemini), `GROQ_API_KEY`★ if `GENERATION_PROVIDER=groq`, `ARCHITECTURE_DIAGRAM_GENERATION_PROVIDER=gemini` if `GENERATION_PROVIDER=groq` (a diagram prompt is too large for a Groq model's per-request limit; Ask AI is unaffected), `GITHUB_TOKEN`★ (recommended: unauthenticated GitHub calls are 60/hour for everyone), `GUEST_SHOWCASE_REPOSITORIES`, plus the guards in `render.yaml`. The app refuses to start in production if URLs are not https, the OAuth pair is missing, `WEB_BASE_URL` is not in `WEB_ORIGINS`, or the database password is the local one.

**Vercel**: `NEXT_PUBLIC_API_URL`, `API_ORIGIN`. No secrets.

## Free-tier guards

Existing, server-side in Redis: AI questions per user per day and burst, global daily AI budget, global AI concurrency, imports per user per hour, concurrent imports per user, repositories per user, embedding/diagram/architecture jobs per user per hour, per-IP login and guest-read limits. Added for the demo (both default to off so local behaviour is unchanged): `IMPORT_GLOBAL_DAILY_LIMIT` (new imports across everyone per 24 h, refunded if the enqueue fails) and `MAX_TOTAL_REPOSITORIES` (hard ceiling on stored repositories). Joining an already-stored repository costs neither. `EMBEDDING_MAX_CHUNKS_PER_REPOSITORY=3000` caps how many chunks of one repository are embedded (code default 10,000; the variable name is the setting name in upper case, with no prefix). It is in `render.yaml`, but Render only applies that file on a Blueprint sync, so also set it under the service's Environment tab; a repository with more chunks keeps keyword search for the rest. Worker concurrency is 1 by construction (jobs run one at a time). When a quota is hit the API returns 429/403 with a plain message the UI shows. If the worker is down, an import stays "queued" and is marked failed by the stale sweeper after `INGESTION_STALE_QUEUED_SECONDS` (300 s here), with a retry prompt.

## Sample repositories (ClassLens, Wanderlust, one small public repo)

No code or authorization change is needed; the existing showcase allow-list does it.

1. Deploy with `GUEST_SHOWCASE_REPOSITORIES` empty. Sign in yourself and import the three repositories, wait for ingestion, then (signed in as a member) build embeddings and generate the architecture diagram for each. All of this is cached in Neon.
2. Set `GUEST_SHOWCASE_REPOSITORIES=owner/classlens,rejankarki1/wanderlust,<small/repo>` on Render (exact `owner/name`, case-insensitive) and redeploy.

Result: signed-out visitors can browse repositories, Code Explorer, architecture and cached diagrams, read-only, rate-limited per IP. **Security implications:** only listed repositories are readable without sign-in, and they are public GitHub repositories anyway; everything else still returns 401/404. Ask AI, search, conversations and any job that spends AI budget stay member-only: a signed-in visitor clicks "Add to my repositories" (free for stored repositories) and then asks questions against your shared embeddings, subject to their personal and the global quotas. A member of a showcase repository can also re-trigger its diagram (within `ARCHITECTURE_DIAGRAM_HOURLY_LIMIT`), which replaces the cached one; keep that limit low. The Wanderlust fixture under `apps/web/public/dev-fixtures/` is a static development file and is not part of this flow.

## Smoke test after deploying

1. `https://<svc>.onrender.com/health` → `{"status":"ok"}`; `/health/ready` → database and redis `ok` (the first request after idle takes ~50 s: cold start).
2. `https://<app>.vercel.app/api/health/ready` → same JSON (proves the rewrite).
3. `https://<app>.vercel.app/api/docs` → 404 (docs are off in production).
4. Open the site signed out → sample repositories are listed (after step "Sample repositories").
5. Sign in with GitHub → returned to `/repositories`; DevTools → Application → Cookies shows `__Host-rl_session` (HttpOnly, Secure) on the Vercel domain only.
6. Code Explorer on a sample: files list and open a file.
7. Add a sample repository, Ask AI a question: the answer streams token by token and cites `path:line`.
8. Architecture tab shows the cached diagram.
9. Import a small public repository: status goes queued → in progress → completed (worker working). Sign out works (CSRF).
10. Render logs show no `Unsafe production configuration` and show `rq.worker` lines.

## Updating an existing deployment

This release needs **no database migration**: the Alembic head is still `f1c4a7b9e820`. Check that the production database is at that revision before deploying (from the repository root, with the real URL set only in your shell; paste Neon's string as shown, the app converts `sslmode` to asyncpg's `ssl`):

```bash
DATABASE_URL='postgresql://…neon…?sslmode=require' REDIS_URL='redis://127.0.0.1:6379/15' \
  apps/api/venv/bin/alembic -c alembic.ini current      # expect f1c4a7b9e820 (head)
```

1. In Neon, create a branch of the production branch as a restore point.
2. Confirm on Render: `EMBEDDING_MAX_CHUNKS_PER_REPOSITORY=3000`.
3. Merge to `main`. Render has auto-deploy off: use *Manual Deploy → Deploy latest commit*. Vercel deploys from `main` on its own.
4. Run the checklist above (health, sign-in, Ask AI with citations, Architecture tab, import status).
5. Rollback: redeploy the previous commit in Render and Vercel. No schema change is involved.

## Limitations and unverified assumptions

- **Verified locally** (Docker, throwaway Postgres+pgvector and Redis): the image builds; migrations run on an empty pgvector database; `/health` and `/health/ready` respond; two real ingestions (itsdangerous, flask: 223 files) completed with API + worker under a hard 512 MB limit, peak ≈ 200 MiB, no OOM kill; `docker stop` stops both processes in ~1 s with exit 0; a dying child takes the service down visibly. The Next.js rewrite was checked with `next start` against a stub: redirects, `Set-Cookie` and an SSE stream pass through incrementally.
- **Not verified** (needs your accounts): real Neon/Redis connections and TLS, real GitHub OAuth through the Vercel rewrite, Gemini/Groq calls, and Ask AI streaming through *Vercel's* proxy (only `next start` was tested). Vercel may limit how long a proxied response may stay open; if long answers are cut, the fallback is calling Render directly, which then needs the cross-site cookie design this deployment avoids.
- **Memory**: idle ≈ 176 MB; ingestion peak ≈ 200 MiB for a 223-file repository. Embedding builds and diagram generation were not measured (no provider keys). A large repository (`GITHUB_ARCHIVE_MAX_BYTES` caps archives at 10 MB here) is the likeliest way to exceed 512 MB. The combined setup is best-effort.
- **Sleeping**: Render Free stops the service after ~15 min without HTTP traffic and takes ~50 s to wake; the worker stops with it, so queued work waits for the next visit. A job running when the instance is stopped is recovered by the stale sweeper and can be retried. Redis and Neon are independent and keep their data.
- **Per-IP limits**: behind Vercel and Render the app sees proxy addresses. `TRUSTED_PROXIES` is left empty (X-Forwarded-For is not believed), so per-IP login and guest limits act as one shared bucket for all visitors. That is safe (it can only over-limit) but a burst of guests can lock others out for a minute. Setting it requires the proxies' real address ranges, which could not be verified here.
- **Redis**: `redis-py`/RQ speak plain Redis protocol, so any TCP/TLS Redis works; Upstash's command quota is the main risk (see above).
- Architecture-diagram generation spends AI budget with only per-user hourly limits (no global daily cap); keep `ARCHITECTURE_DIAGRAM_HOURLY_LIMIT` small and watch provider usage.
