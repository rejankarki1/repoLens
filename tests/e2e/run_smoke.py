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


async def recreate_database(name: str) -> None:
    url = make_url(get_settings().database_url)
    admin = await asyncpg.connect(user=url.username, password=url.password, host=url.host, port=url.port, database="postgres")
    try:
        await admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        await admin.execute(f"CREATE DATABASE {name}")
    finally:
        await admin.close()


async def drop_database(name: str) -> None:
    url = make_url(get_settings().database_url)
    admin = await asyncpg.connect(user=url.username, password=url.password, host=url.host, port=url.port, database="postgres")
    try:
        await admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
    finally:
        await admin.close()


def main() -> int:
    db_name = os.environ.get("E2E_DATABASE", "repolens_e2e")
    dev = make_url(get_settings().database_url)
    redis = make_url(get_settings().redis_url).set(database="9").render_as_string(hide_password=False)  # not the dev Redis DB
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
        # a scratch Redis database: start empty
        subprocess.run([sys.executable, "-c", "import redis,os; redis.Redis.from_url(os.environ['REDIS_URL']).flushdb()"], cwd=ROOT, env=env, check=True)
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
        subprocess.run([sys.executable, "-c", "import redis,os; redis.Redis.from_url(os.environ['REDIS_URL']).flushdb()"], cwd=ROOT, env=env, check=False)
        try:
            asyncio.run(drop_database(db_name))
        except Exception as exc:  # best effort
            print(f"could not drop {db_name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
