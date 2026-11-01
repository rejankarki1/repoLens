# Verifying real GitHub OAuth

A manual procedure to prove that sign-in works with a real GitHub OAuth App, without touching your development database, your real Redis queue, or any secret in Git. Automated tests and the browser smoke test use a *fake* GitHub; this is the only check that talks to the real one.

**Status of this procedure: not yet performed.** It needs an OAuth App you create. Nothing here runs automatically.

## Rules that keep it safe

- Work on a **copy** of the development database (`repolens_oauth_check`), **Redis database 8** and the **queue `oauth_check`**. Your real database, Redis database 0 and the `ingestion` queue are never used, so your real worker cannot pick up these jobs.
- Never run `alembic upgrade` or `claim_local_data` with your normal `.env` in force. Every command below runs in a shell where the isolated settings are loaded and checked first (step 3).
- Secrets live only in `.env.oauth-check` (ignored by Git). Never `echo` them, never run bare `env` or `printenv` in that shell, never paste them into chat, issues or logs.
- The OAuth App requests **no scopes**. If GitHub's consent page lists anything beyond public information, stop.
- Use `localhost` everywhere, never `127.0.0.1`: the session cookie needs the web app and the API on the same site.

## 1. Register a development OAuth App

GitHub → Settings → Developer settings → OAuth Apps → **New OAuth App**.

| Field | Value |
| --- | --- |
| Application name | anything, for example `RepoLens (local check)` |
| Homepage URL | `http://localhost:3000` |
| Authorization callback URL | `http://localhost:8765/auth/github/callback` (exact; this is `<API_PUBLIC_URL>/auth/github/callback`) |

Create it, then **Generate a new client secret**. Use the standard ports 3000 and 8765 so the callback matches. Stop your normal dev API and web app first so the ports are free. Use a separate OAuth App for production later.

## 2. Create the isolated settings file

```bash
cat > .env.oauth-check <<'EOF'
DATABASE_URL=postgresql+asyncpg://repolens:local_dev_password@127.0.0.1:5433/repolens_oauth_check
REDIS_URL=redis://127.0.0.1:6379/8
INGESTION_QUEUE_NAME=oauth_check
WEB_BASE_URL=http://localhost:3000
WEB_ORIGINS=http://localhost:3000
API_PUBLIC_URL=http://localhost:8765
GITHUB_OAUTH_CLIENT_ID=
GITHUB_OAUTH_CLIENT_SECRET=
GUEST_SHOWCASE_REPOSITORIES=pallets/itsdangerous
GEMINI_API_KEY=
GROQ_API_KEY=
EOF
chmod 600 .env.oauth-check
git check-ignore -v .env.oauth-check      # must print a rule; if it prints nothing, stop
```

Open the file in an editor and fill in `GITHUB_OAUTH_CLIENT_ID` and `GITHUB_OAUTH_CLIENT_SECRET`. The AI keys are deliberately blank so this check cannot spend money; Ask AI is not part of it (it is covered elsewhere). Adjust the `DATABASE_URL` user and password if your `.env` uses different ones.

## 3. Load the settings and run the preflight

```bash
set -a; . ./.env.oauth-check; set +a
PYTHONPATH=. apps/api/venv/bin/python - <<'PY'
from sqlalchemy.engine import make_url
from apps.api.config import get_settings

s = get_settings()
db = make_url(s.database_url)
print("database :", db.host, db.database)
print("redis db :", s.redis_url.rsplit("/", 1)[-1], "| queue:", s.ingestion_queue_name)
print("oauth    :", "configured" if s.github_login_enabled else "NOT configured", "| callback:", s.github_oauth_redirect_uri)
print("AI keys  :", "SET (unexpected)" if s.embeddings_enabled or (s.groq_api_key and s.groq_api_key.get_secret_value()) else "blank")
assert db.database == "repolens_oauth_check" and s.redis_url.endswith("/8") and s.ingestion_queue_name == "oauth_check", "NOT the isolated setup: stop here"
print("OK: the isolated setup is in force")
PY
```

Expected: database `repolens_oauth_check`, redis db `8`, queue `oauth_check`, OAuth `configured`, callback `http://localhost:8765/auth/github/callback`, AI keys `blank`, then `OK`. Anything else: stop and fix `.env.oauth-check`. Run this check again in every new shell before any command below.

## 4. Create the database copy and migrate only the copy

This reads your development database (`pg_dump`) and writes only to the new one.

```bash
docker compose exec -T postgres psql -U repolens -d postgres -c "CREATE DATABASE repolens_oauth_check"
docker compose exec -T postgres pg_dump -U repolens -d repolens --no-owner \
  | docker compose exec -T postgres psql -U repolens -d repolens_oauth_check -q
apps/api/venv/bin/alembic current        # b84f2c6d1e07 (the old schema), on the copy
apps/api/venv/bin/alembic upgrade head   # the copy only: DATABASE_URL points at it
apps/api/venv/bin/alembic current        # d2b8f4e0a513 (head)
```

If you skip the dump, an empty database also works, but the data-claim check (step 6.9) then has nothing to claim.

## 5. Start the stack (three terminals)

In the API and worker terminals, load the settings and run the preflight (step 3) first. The web terminal does not need the secrets: do **not** load the file there.

```bash
apps/api/venv/bin/uvicorn apps.api.main:app --port 8765
apps/api/venv/bin/python -m apps.api.worker
cd apps/web && NEXT_PUBLIC_API_URL=http://localhost:8765 npm run dev
```

Open `http://localhost:3000`. `GET http://localhost:8765/auth/me` should say `"github_login_enabled": true`.

## 6. Acceptance checks

Use a normal browser window for the signed-in user and a private window for visitors. Tick each box only after seeing the result.

**Login**
- [ ] 6.1 As a visitor the sidebar says "Browsing as a guest" and the showcase repository is listed read-only; Ask AI and Re-analyze are not offered.
- [ ] 6.2 "Continue with GitHub" goes to `github.com`, whose consent page lists **only public information** and no repository access. The address bar shows a `code_challenge` parameter and no `scope`.
- [ ] 6.3 After approving, you land back on `http://localhost:3000/...` signed in, with your real GitHub avatar, name and `@login` in the sidebar.
- [ ] 6.4 Cancel: sign out, start again and press **Cancel** on GitHub. You return signed out with "GitHub sign-in was cancelled."
- [ ] 6.5 DevTools → Application → Cookies (`localhost`): `rl_session` is **HttpOnly**, SameSite **Lax**, no Domain; `document.cookie` in the console prints an empty string; Local and Session Storage are empty.

**URL import continuation**
- [ ] 6.6 In the private window, paste `https://github.com/octocat/Spoon-Knife` into the box. You are asked to sign in and the prompt names `octocat/Spoon-Knife`; nothing is imported yet. Continue with GitHub: you return to the repository's workspace, it is ingested by the worker, and the address bar no longer holds the pasted URL.

**Repository access**
- [ ] 6.7 Second account (another GitHub user, in a separate browser profile): its dashboard is empty, and opening your repository's `/repos/<id>` URL shows "Repository not found". Pasting `https://github.com/octocat/Spoon-Knife` joins the existing repository without downloading it again. (`octocat/Hello-World` is already in a copy of your development data, so it is not suitable for the import check in 6.6; it would simply be joined.)
- [ ] 6.8 As a visitor, opening a repository that is not in the showcase shows "Sign in to view this repository".

**Local-data claim** (on the copy only; the preflight must have passed in this shell)

- [ ] 6.9 With your own account signed in at least once:
  ```bash
  apps/api/venv/bin/python -m apps.api.scripts.claim_local_data --github-login <your-login>           # dry run: reports counts, changes nothing
  apps/api/venv/bin/python -m apps.api.scripts.claim_local_data --github-login <your-login> --apply
  apps/api/venv/bin/python -m apps.api.scripts.claim_local_data --github-login <your-login> --apply   # repeat: moves 0
  ```
  Reload: your old repositories and conversations appear. The second account still sees none of them.

**Logout and session expiry**
- [ ] 6.10 Logout: click the sign-out icon. The sidebar returns to the guest view and `rl_session` disappears from DevTools. To prove the old cookie is dead, copy its value *before* logging out into a shell variable without printing it (`read -s OLD`), log out, then run `curl -s -b "rl_session=$OLD" http://localhost:8765/auth/me` and expect `"authenticated":false`.
- [ ] 6.11 Expiry: sign in again, then force the session to expire on the copy and reload the page:
  ```bash
  docker compose exec -T postgres psql -U repolens -d repolens_oauth_check -c \
    "UPDATE sessions SET expires_at = now() - interval '1 second' WHERE revoked_at IS NULL"
  ```
  The next request signs you out (the sidebar shows the guest view). For the idle rule, set `last_seen_at = now() - interval '8 days'` instead.
- [ ] 6.12 Sign in with two browsers, choose "logout everywhere" through `POST /auth/logout-all` (needs the CSRF token from `/auth/me`) or revoke in the database, and confirm both are signed out.

**Secrets hygiene**
- [ ] 6.13 `grep -rIlE "gho_[A-Za-z0-9]{20,}" apps docs database` finds nothing (the test suite's fake tokens are not in these paths); `SELECT token_hash FROM sessions LIMIT 3` shows 64-character hex values, not cookie values; the API terminal's log line for the callback reads `code=[redacted]&state=[redacted]`.
- [ ] 6.14 GitHub → Settings → Applications → Authorized OAuth Apps lists your app as having no scopes (public data only). GitHub's exact wording may differ; what matters is that no repository or private-data access is listed.

## 7. Clean up

```bash
# stop the three processes (Ctrl-C), then:
docker compose exec -T postgres psql -U repolens -d postgres -c "DROP DATABASE repolens_oauth_check WITH (FORCE)"
docker compose exec -T redis redis-cli -n 8 flushdb        # database 8 only: it is this procedure's own
rm .env.oauth-check                                          # or keep it, mode 600
```

Revoke the authorization in GitHub (step 6.14 page) if you like. Rotate the client secret if it was ever displayed, pasted or committed. Your real database, database 0 and the `ingestion` queue were never touched.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| GitHub shows `redirect_uri_mismatch` | The OAuth App's callback differs from `API_PUBLIC_URL/auth/github/callback` (scheme, host, port or path). |
| Back on the site with `?auth_error=invalid_state` text ("could not be verified") | The state cookie was not sent back: mixed `localhost` and `127.0.0.1`, a blocked cookie, or a sign-in older than 10 minutes. |
| "GitHub sign-in is not configured" | `GITHUB_OAUTH_CLIENT_ID` or `GITHUB_OAUTH_CLIENT_SECRET` is empty in the shell that started the API (load the file, then run the preflight). |
| "GitHub sign-in did not complete" after approving | The client secret or client id is wrong, or the one-time code was already used. Start again. The API log records only that the exchange failed, never your secret. |
| Signed in on the callback, signed out on the page | The web app calls a different host than the cookie's (`NEXT_PUBLIC_API_URL` must be `http://localhost:8765`). |
| "Too many sign-in attempts" | The per-IP sign-in limit (20 per minute by default) was hit; wait a minute. |

## Recording the result

When every box is ticked, add a line to `docs/release-baseline.md` ("Verified results" and "Known limitations") stating the date and that real GitHub OAuth was exercised, and commit it. Until then that file keeps saying it has not been.
