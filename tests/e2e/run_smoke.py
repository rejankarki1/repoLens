"""Boot an isolated RepoLens stack and run the browser smoke test against it.

    python -m tests.e2e.run_smoke

Needs PostgreSQL with pgvector, Redis, Google Chrome (or CHROME_BIN), Node 22 and the web app's dependencies.
It never touches development data: it creates and drops its own database (E2E_DATABASE, default repolens_e2e),
uses Redis database 9 with its own queue, binds its own ports, and talks to a FAKE GitHub (tests/e2e/fake_github.py),
so no real OAuth credentials, GitHub access or AI keys are involved.
"""

import asyncio
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import asyncpg
from sqlalchemy.engine import make_url

from apps.api.config import get_settings

ROOT = Path(__file__).resolve().parents[2]
DB_MARKER = "repolens-disposable-e2e-database"  # a database comment: only databases carrying it are ever dropped
REDIS_MARKER = "repolens:e2e-database-marker"  # only a Redis database that is empty or carries this key is ever flushed
REDIS_DB = 9
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
PORTS = {"API": 8790, "WEB": 3190, "GH": 8791, "CDP": 9390}
CHROME_CANDIDATES = [
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def chrome() -> str:
    for candidate in [os.environ.get("CHROME_BIN", ""), *CHROME_CANDIDATES]:
        if candidate and (shutil.which(candidate) or Path(candidate).exists()):
            return shutil.which(candidate) or candidate
    sys.exit("Chrome was not found; set CHROME_BIN")


def wait_for(url: str, seconds: int = 90) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2).read()
            return
        except Exception:
            time.sleep(0.5)
    sys.exit(f"timed out waiting for {url}")


async def _admin(url):
    return await asyncpg.connect(user=url.username, password=url.password, host=url.host, port=url.port, database="postgres")


async def recreate_database(name: str) -> None:
    """(Re)create the disposable database, but never drop one that this script did not create."""
    admin = await _admin(make_url(get_settings().database_url))
    try:
        exists = await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name)
        if exists:
            comment = await admin.fetchval("SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = $1", name)
            if comment != DB_MARKER:
                sys.exit(f"Refusing to drop database {name!r}: it was not created by this smoke test. Set E2E_DATABASE to a free name.")
            await admin.execute(f"DROP DATABASE {name} WITH (FORCE)")
        await admin.execute(f"CREATE DATABASE {name}")
        await admin.execute(f"COMMENT ON DATABASE {name} IS '{DB_MARKER}'")
    finally:
        await admin.close()


async def drop_database(name: str) -> None:
    admin = await _admin(make_url(get_settings().database_url))
    try:
        comment = await admin.fetchval("SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = $1", name)
        if comment == DB_MARKER:  # only ever drop what we created
            await admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
    finally:
        await admin.close()


def claim_redis(url: str) -> None:
    """Refuse to touch a Redis database that holds somebody else's data; otherwise mark it as ours."""
    import redis

    client = redis.Redis.from_url(url)
    if client.dbsize() and not client.exists(REDIS_MARKER):
        sys.exit(f"Refusing to use Redis database {REDIS_DB}: it is not empty and was not created by the smoke test.")
    client.set(REDIS_MARKER, "1")


def release_redis(url: str) -> None:
    import redis

    client = redis.Redis.from_url(url)
    if client.exists(REDIS_MARKER):  # flush only what we claimed
        client.flushdb()


def main() -> int:
    db_name = os.environ.get("E2E_DATABASE", "repolens_e2e")
    dev = make_url(get_settings().database_url)
    redis = make_url(get_settings().redis_url).set(database=str(REDIS_DB)).render_as_string(hide_password=False)  # not the dev Redis DB
    redis_host = make_url(redis).host
    if (dev.host not in LOOPBACK or redis_host not in LOOPBACK) and os.environ.get("REPOLENS_TEST_ALLOW_REMOTE") != "1":
        sys.exit("Refusing to run: the database or Redis host is not local (set REPOLENS_TEST_ALLOW_REMOTE=1 for a disposable CI service).")
    api, web, gh, cdp = (PORTS[k] for k in ("API", "WEB", "GH", "CDP"))
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "DATABASE_URL": dev.set(database=db_name).render_as_string(hide_password=False),
        "REDIS_URL": redis,
        "INGESTION_QUEUE_NAME": "e2e_" + secrets.token_hex(4),  # a developer's own worker never sees these jobs
        "INGESTION_SWEEP_INTERVAL_SECONDS": "0",
        "ENVIRONMENT": "development",
        "WEB_ORIGINS": f"http://localhost:{web}",
        "WEB_BASE_URL": f"http://localhost:{web}",
        "API_PUBLIC_URL": f"http://localhost:{api}",
        "GITHUB_OAUTH_CLIENT_ID": "e2e-client", "GITHUB_OAUTH_CLIENT_SECRET": "e2e-secret",
        "GITHUB_OAUTH_AUTHORIZE_URL": f"http://localhost:{gh}/login/oauth/authorize",
        "GITHUB_OAUTH_TOKEN_URL": f"http://localhost:{gh}/login/oauth/access_token",
        "GITHUB_API_BASE_URL": f"http://localhost:{gh}",
        "GUEST_SHOWCASE_REPOSITORIES": "octo/demo",
        "GEMINI_API_KEY": "", "GROQ_API_KEY": "", "GITHUB_TOKEN": "",
        "NEXT_PUBLIC_API_URL": f"http://localhost:{api}",
        "NEXT_TELEMETRY_DISABLED": "1",
    }
    procs: list[subprocess.Popen] = []

    def start(cmd: list[str], cwd: Path = ROOT, **extra) -> subprocess.Popen:
        p = subprocess.Popen(cmd, cwd=cwd, env={**env, **extra}, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE if os.environ.get("E2E_DEBUG") else subprocess.DEVNULL)
        procs.append(p)
        return p

    profile = ROOT / ".e2e-chrome-profile"
    try:
        asyncio.run(recreate_database(db_name))
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True, capture_output=True)
        claim_redis(redis)  # exits instead of flushing a Redis database that is not ours
        start([sys.executable, "-m", "tests.e2e.fake_github", "--port", str(gh)])
        start([sys.executable, "-m", "uvicorn", "apps.api.main:app", "--port", str(api)])
        start([sys.executable, "-m", "apps.api.worker"])
        subprocess.run(["npm", "run", "build"], cwd=ROOT / "apps/web", env=env, check=True, capture_output=True)
        start(["npx", "next", "start", "-p", str(web)], cwd=ROOT / "apps/web")
        start([chrome(), "--headless=new", f"--remote-debugging-port={cdp}", f"--user-data-dir={profile}", "--no-first-run",
               "--no-default-browser-check", "--disable-extensions", "--no-sandbox", "--window-size=1440,900", "about:blank"])
        wait_for(f"http://localhost:{api}/health/ready")
        wait_for(f"http://localhost:{web}/")
        wait_for(f"http://127.0.0.1:{cdp}/json/version")
        result = subprocess.run(
            ["node", str(ROOT / "tests/e2e/smoke.mjs")],
            env={**env, "E2E_WEB": f"http://localhost:{web}", "E2E_API": f"http://localhost:{api}", "E2E_GH": f"http://localhost:{gh}", "E2E_CDP": str(cdp)},
        )
        return result.returncode
    finally:
        for p in reversed(procs):
            p.send_signal(signal.SIGTERM)
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        shutil.rmtree(profile, ignore_errors=True)
        try:
            release_redis(redis)
        except Exception as exc:  # best effort
            print(f"could not release Redis database {REDIS_DB}: {exc}", file=sys.stderr)
        try:
            asyncio.run(drop_database(db_name))
        except Exception as exc:  # best effort
            print(f"could not drop {db_name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
