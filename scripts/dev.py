"""Start, stop and check the local RepoLens OAuth-check stack from ONE merged configuration.

Recommended: four terminals, each in the foreground so its logs stay visible (Ctrl-C stops that process):
    scripts/dev-docker.sh [--detach]     PostgreSQL + Redis (then follows their logs)
    scripts/dev-api.sh [--reload]        FastAPI on :8765
    scripts/dev-worker.sh                RQ worker on the isolated queue
    scripts/dev-web.sh                   Next.js on :3000
    scripts/dev-status.sh [--offline]    read-only verification table (any terminal, any time)
All-in-one (background, logs in .dev/logs):
    scripts/dev-start.sh [--offline]  /  scripts/dev-stop.sh [--docker] [--force]

Configuration model (see README "Start and stop"):
  .env               shared base: Docker ports, Gemini / Groq keys and models.
  .env.oauth-check   overlay with ONLY what differs: database, Redis db, queue, GitHub OAuth app, URLs.
The two files are merged here with python-dotenv (the parser pydantic-settings uses), the overlay wins, and every
child process is started with exactly that environment plus a short allow-list, never with whatever happens to be
exported in the calling shell. API and worker therefore always agree. Nothing here runs migrations, creates or drops
databases, touches Redis keys, or calls a paid provider; secret values are never printed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / "apps/api/venv/bin/python"
BASE_FILE = ROOT / ".env"
OVERLAY_FILE = ROOT / ".env.oauth-check"
STATE = ROOT / ".dev"
PIDS, LOGS = STATE / "pids", STATE / "logs"

EXPECTED_DB = "repolens_oauth_check"
EXPECTED_REDIS_DB = "8"
EXPECTED_QUEUE = "oauth_check"
API_PORT, WEB_PORT = 8765, 3000
API_URL, WEB_URL = f"http://localhost:{API_PORT}", f"http://localhost:{WEB_PORT}"
CALLBACK = f"{API_URL}/auth/github/callback"
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
STACK_MARKER = "REPOLENS_DEV_STACK"  # set in every child we start; how we recognise our own processes
ALLOWED_PARENT_ENV = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TMPDIR",
    "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "SSL_CERT_FILE",
)
AI_KEYS = ("GEMINI_API_KEY", "GROQ_API_KEY")
SERVICES = ("api", "worker", "web")
TERM_WAIT_SECONDS = 30
# Only a Python interpreter actually running `-m apps.api.worker` is a worker (not a shell or grep that mentions it).
WORKER_COMMAND = re.compile(r"(?:^|/)[Pp]ython[0-9.]*\s+-m\s+apps\.api\.worker(?:\s|$)")


# --------------------------------------------------------------------------------------------------------------------
# Pure functions: configuration
# --------------------------------------------------------------------------------------------------------------------

def load_env(base: Path, overlay: Path) -> dict[str, str]:
    """`.env` first, then the overlay on top. Missing files are an error; `None` values (bare `KEY`) are dropped."""
    merged: dict[str, str] = {}
    for path in (base, overlay):
        if not path.is_file():
            raise FileNotFoundError(f"{path.name} not found")
        merged.update({k: v for k, v in dotenv_values(path).items() if v is not None})
    return merged


def clean_env(parent: dict[str, str], extra: dict[str, str] | None = None) -> dict[str, str]:
    """The only environment a child ever sees: a short allow-list from the calling shell, then `extra`."""
    env = {k: parent[k] for k in ALLOWED_PARENT_ENV if k in parent}
    env.update(extra or {})
    return env


def service_env(merged: dict[str, str], parent: dict[str, str]) -> dict[str, str]:
    """API and worker: the full merged configuration. Identical by construction."""
    return clean_env(parent, {**merged, STACK_MARKER: "1", "PYTHONPATH": str(ROOT)})


def web_env(parent: dict[str, str]) -> dict[str, str]:
    """The web app holds no secrets: only where the API is."""
    return clean_env(parent, {"NEXT_PUBLIC_API_URL": API_URL, STACK_MARKER: "1"})


def _db_name(url: str) -> str:
    return urlsplit(url.replace("+asyncpg", "")).path.lstrip("/")


def _redis_db(url: str) -> str:
    return urlsplit(url).path.lstrip("/") or "0"


def _host(url: str) -> str:
    return urlsplit(url.replace("+asyncpg", "")).hostname or ""


def check_config(merged: dict[str, str], overlay: dict[str, str] | None = None) -> tuple[list[str], list[str]]:
    """(problems, warnings). Any problem means: do not start anything."""
    problems: list[str] = []
    warnings: list[str] = []
    get = lambda k: merged.get(k, "")  # noqa: E731

    if _db_name(get("DATABASE_URL")) != EXPECTED_DB:
        problems.append(f"DATABASE_URL must point at {EXPECTED_DB!r} (got {_db_name(get('DATABASE_URL')) or 'nothing'!r}); fix it in .env.oauth-check")
    if _host(get("DATABASE_URL")) not in LOOPBACK or _host(get("REDIS_URL")) not in LOOPBACK:
        problems.append("DATABASE_URL and REDIS_URL must point at this machine")
    if _redis_db(get("REDIS_URL")) != EXPECTED_REDIS_DB:
        problems.append(f"REDIS_URL must use database {EXPECTED_REDIS_DB} (got {_redis_db(get('REDIS_URL'))})")
    if get("INGESTION_QUEUE_NAME") != EXPECTED_QUEUE:
        problems.append(f"INGESTION_QUEUE_NAME must be {EXPECTED_QUEUE!r} (got {get('INGESTION_QUEUE_NAME') or 'nothing'!r}): the default queue is your real one")
    if get("ENVIRONMENT") == "production":
        problems.append("ENVIRONMENT=production is not for this local stack")

    client_id, secret = get("GITHUB_OAUTH_CLIENT_ID"), get("GITHUB_OAUTH_CLIENT_SECRET")
    if not client_id or client_id.startswith("PASTE_"):
        problems.append("GITHUB_OAUTH_CLIENT_ID is empty or still a placeholder")
    elif not re.fullmatch(r"Ov23[A-Za-z0-9]{16}", client_id):
        problems.append("GITHUB_OAUTH_CLIENT_ID has the wrong shape (an OAuth App id is 20 characters starting 'Ov23'); check for a stray character")
    if not secret or secret.startswith("PASTE_"):
        problems.append("GITHUB_OAUTH_CLIENT_SECRET is empty or still a placeholder")
    elif not re.fullmatch(r"[0-9a-f]{40}", secret):
        problems.append("GITHUB_OAUTH_CLIENT_SECRET has the wrong shape (40 hex characters); check for stray characters")

    if get("WEB_BASE_URL").rstrip("/") != WEB_URL:
        problems.append(f"WEB_BASE_URL must be {WEB_URL}")
    if get("API_PUBLIC_URL").rstrip("/") != API_URL:
        problems.append(f"API_PUBLIC_URL must be {API_URL}")
    origins = [o.strip().rstrip("/") for o in get("WEB_ORIGINS").split(",") if o.strip()]
    if WEB_URL not in origins:
        problems.append(f"WEB_ORIGINS must include {WEB_URL}")
    if any("127.0.0.1" in v for v in (get("WEB_BASE_URL"), get("API_PUBLIC_URL"), get("WEB_ORIGINS"))):
        problems.append("use 'localhost', not '127.0.0.1', in browser-facing URLs (the session cookie needs one site)")

    provider = get("GENERATION_PROVIDER") or "gemini"
    if provider not in ("gemini", "groq"):
        problems.append(f"GENERATION_PROVIDER must be 'gemini' or 'groq' (got {provider!r})")
    if provider == "groq" and not get("GROQ_API_KEY"):
        problems.append("GENERATION_PROVIDER=groq but GROQ_API_KEY is not set in .env")
    if not get("GEMINI_API_KEY"):
        problems.append("GEMINI_API_KEY is not set in .env: embeddings and hybrid search would be off" + (" and so would answers" if provider == "gemini" else ""))

    if overlay is not None:
        shadowing = [k for k in AI_KEYS if k in overlay]
        if shadowing:
            warnings.append(f".env.oauth-check sets {', '.join(shadowing)}; it overrides .env. Delete those lines so the keys come from .env")
        if "GITHUB_TOKEN" in overlay and not overlay["GITHUB_TOKEN"]:
            warnings.append("GITHUB_TOKEN is blank in .env.oauth-check (harmless; the line can be removed)")
    return problems, warnings


def alembic_revisions(output: str) -> list[str]:
    """Revision ids from `alembic current` / `alembic heads` output (ignores INFO log lines)."""
    ids = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith(("INFO", "WARNING", "ERROR", "Traceback")):
            continue
        match = re.match(r"^([0-9A-Za-z_]+)(?: \(head\))?$", line)
        if match:
            ids.append(match.group(1))
    return ids


def pick_env(ps_output: str, keys: tuple[str, ...]) -> dict[str, str]:
    """Pull KEY=value pairs out of `ps eww` output (a process's environment on macOS and Linux)."""
    found = {}
    for key in keys:
        match = re.search(rf"(?:^|\s){key}=(\S*)", ps_output)
        if match is not None:
            found[key] = match.group(1)
    return found


def describe_config(env: dict[str, str]) -> str:
    """Names only: which database, Redis db and queue a process is configured for."""
    db = _db_name(env["DATABASE_URL"]) if "DATABASE_URL" in env else "?"
    redis = _redis_db(env["REDIS_URL"]) if "REDIS_URL" in env else "0 (default)"
    queue = env.get("INGESTION_QUEUE_NAME") or "ingestion (default)"
    return f"database={db or '?'} redis_db={redis} queue={queue}"


def fingerprint(merged: dict[str, str]) -> str:
    """A short hash of the whole merged configuration. API and worker print it, so two terminals can be compared at a
    glance without showing any value (8 hex characters reveal nothing useful about a secret)."""
    return hashlib.sha256("\n".join(f"{k}={merged[k]}" for k in sorted(merged)).encode()).hexdigest()[:8]


def banner(role: str, merged: dict[str, str]) -> str:
    """What a freshly started process announces about itself: names and yes/no only, never a credential."""
    provider = merged.get("GENERATION_PROVIDER") or "gemini"
    model = merged.get("GROQ_GENERATION_MODEL" if provider == "groq" else "GEMINI_GENERATION_MODEL", "default")
    yes = lambda ok: "yes" if ok else "NO"  # noqa: E731
    oauth = bool(merged.get("GITHUB_OAUTH_CLIENT_ID") and merged.get("GITHUB_OAUTH_CLIENT_SECRET"))
    return "\n".join((
        f"[RepoLens dev] {role}",
        f"  storage      {describe_config(merged)}",
        f"  answers      {provider} {model} (key set: {yes(bool(merged.get('GROQ_API_KEY' if provider == 'groq' else 'GEMINI_API_KEY')))})",
        f"  embeddings   {merged.get('EMBEDDING_MODEL') or 'gemini-embedding-001'} (Gemini key set: {yes(bool(merged.get('GEMINI_API_KEY')))})",
        f"  GitHub OAuth configured: {yes(oauth)}   config fingerprint: {fingerprint(merged)}",
        "  environment  .env + .env.oauth-check merged; nothing inherited from this shell",
    ))


# --------------------------------------------------------------------------------------------------------------------
# Processes
# --------------------------------------------------------------------------------------------------------------------

def run(cmd: list[str], env: dict[str, str] | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)


def ps_environment(pid: int) -> str:
    out = run(["ps", "eww", "-p", str(pid)], timeout=10)
    return out.stdout if out.returncode == 0 else ""


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def pgid_of(pid: int) -> int | None:
    try:
        return os.getpgid(pid)
    except ProcessLookupError:
        return None


def proc_start(pid: int) -> str:
    return run(["ps", "-o", "lstart=", "-p", str(pid)], timeout=10).stdout.strip()


def recorded(name: str) -> tuple[int, str | None] | None:
    """(pid, start time) written when we started the service. Older pid files hold the pid only."""
    try:
        first, *rest = (PIDS / f"{name}.pid").read_text().splitlines()
        return int(first.strip()), (rest[0].strip() if rest else None)
    except (OSError, ValueError, IndexError):
        return None


def is_ours(name: str) -> bool:
    """The recorded process is alive AND is the very process we started (same start time, so a recycled pid never
    matches). Node rewrites its process title, so the environment is not a reliable marker for the web app."""
    entry = recorded(name)
    if entry is None or not pid_alive(entry[0]):
        return False
    pid, started = entry
    if started is not None:
        return proc_start(pid) == started
    return f"{STACK_MARKER}=1" in ps_environment(pid)  # pid file from an older run


def read_pid(name: str) -> int | None:
    entry = recorded(name)
    return entry[0] if entry else None


def our_pgids() -> set[int]:
    pgids = set()
    for name in SERVICES:
        pid = read_pid(name)
        if pid and is_ours(name) and (pg := pgid_of(pid)):
            pgids.add(pg)
    return pgids


def listeners(port: int) -> list[int]:
    out = run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], timeout=10)
    return sorted({int(p) for p in out.stdout.split() if p.isdigit()})


def command_of(pid: int) -> str:
    out = run(["ps", "-o", "command=", "-p", str(pid)], timeout=10).stdout.strip()
    out = re.sub(r"\S*/(Python\.app/Contents/MacOS/Python|python[0-9.]*|uvicorn)(?=\s|$)", lambda m: m.group(1).split("/")[-1].lower(), out)
    return out[:90]


def find_workers() -> list[tuple[int, int, dict[str, str]]]:
    """Every RQ worker on this machine: (pid, process group, environment names), one entry per process group."""
    out = run(["ps", "-eo", "pid,command"], timeout=10).stdout
    seen, found = set(), []
    for line in out.splitlines():
        match = re.match(r"\s*(\d+)\s+(.*)$", line)
        if not match or not WORKER_COMMAND.search(match.group(2)):
            continue
        pid = int(match.group(1))
        group = pgid_of(pid)
        if group is None or group in seen:
            continue
        seen.add(group)
        found.append((pid, group, pick_env(ps_environment(pid), ("DATABASE_URL", "REDIS_URL", "INGESTION_QUEUE_NAME", "GEMINI_API_KEY"))))
    return found


# --------------------------------------------------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------------------------------------------------

@dataclass
class Report:
    failures: int = 0
    lines: list[str] = field(default_factory=list)

    def ok(self, label: str, detail: str = "") -> None:
        self._add("ok  ", label, detail)

    def warn(self, label: str, detail: str = "") -> None:
        self._add("warn", label, detail)

    def fail(self, label: str, detail: str = "") -> None:
        self.failures += 1
        self._add("FAIL", label, detail)

    def _add(self, tag: str, label: str, detail: str) -> None:
        line = f"[{tag}] {label:<34} {detail}".rstrip()
        self.lines.append(line)
        print(line, flush=True)


# --------------------------------------------------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------------------------------------------------

def load_or_exit() -> tuple[dict[str, str], dict[str, str]]:
    try:
        merged = load_env(BASE_FILE, OVERLAY_FILE)
        overlay = {k: v for k, v in dotenv_values(OVERLAY_FILE).items() if v is not None}
    except FileNotFoundError as exc:
        sys.exit(f"{exc}. Create {OVERLAY_FILE.name} from .env.oauth-check.example (see README, 'Start and stop').")
    return merged, overlay


def preflight(merged: dict[str, str], overlay: dict[str, str]) -> bool:
    problems, warnings = check_config(merged, overlay)
    if OVERLAY_FILE.stat().st_mode & 0o077:
        warnings.append(f"{OVERLAY_FILE.name} is readable by other users; run: chmod 600 {OVERLAY_FILE.name}")
    for w in warnings:
        print(f"[warn] {w}")
    for p in problems:
        print(f"[FAIL] {p}")
    if problems:
        print("\nNothing was started.")
        return False
    print(f"[ ok ] configuration       base .env + overlay {OVERLAY_FILE.name}; database, Redis db and queue are the isolated ones")
    return True


def docker_up(env: dict[str, str]) -> bool:
    out = run(["docker", "compose", "up", "-d", "--no-recreate", "--wait"], env=env, timeout=300)
    if out.returncode != 0:
        print(f"[FAIL] docker compose up failed:\n{(out.stderr or out.stdout).strip()[-600:]}")
        return False
    print("[ ok ] docker              PostgreSQL and Redis running (existing containers and volumes untouched)")
    return True


def database_ready(merged: dict[str, str], env: dict[str, str]) -> bool:
    user = merged.get("POSTGRES_USER", "repolens")
    out = run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", user, "-d", "postgres", "-Atc",
               f"select 1 from pg_database where datname='{EXPECTED_DB}'"], env=env, timeout=30)
    if out.stdout.strip() != "1":
        print(f"[FAIL] database {EXPECTED_DB} does not exist. Create it from a copy of your data first (docs/oauth-verification.md, step 4). Nothing was created.")
        return False
    current = alembic_revisions(run([str(PYTHON), "-m", "alembic", "current"], env=env, timeout=60).stdout)
    heads = alembic_revisions(run([str(PYTHON), "-m", "alembic", "heads"], env=env, timeout=60).stdout)
    if not heads or current != heads:
        print(f"[FAIL] {EXPECTED_DB} is at {current or ['no revision']}, code expects {heads}. Migrate it yourself:\n"
              f"       cd {ROOT} && set -a && . ./.env && . ./.env.oauth-check && set +a && apps/api/venv/bin/alembic upgrade head\n"
              "       (nothing was migrated)")
        return False
    print(f"[ ok ] database            {EXPECTED_DB} exists and is at the current schema ({heads[0]})")
    return True


def conflicts(roles: tuple[str, ...], include_ours: bool = False) -> list[str]:
    """Reasons a role cannot start: something already holds its port or its queue. Never kills anything.

    `include_ours` is for starting one service by itself: the all-in-one stack's processes then count as holders too."""
    found = []
    mine = our_pgids()
    for service, port in (("api", API_PORT), ("web", WEB_PORT)):
        if service not in roles:
            continue
        reported: set[int | None] = set()
        for pid in listeners(port):
            group = pgid_of(pid)
            if group in reported or (group in mine and not include_ours):  # a reloader's respawned children share one group
                continue
            reported.add(group)
            holder = group if group and pid_alive(group) else pid  # point at the group leader (the process you started)
            if group in mine:
                found.append(f"port {port} ({service}) is held by the all-in-one stack (pid {holder}); stop it with ./scripts/dev-stop.sh")
                continue
            env = pick_env(ps_environment(holder), ("DATABASE_URL", "REDIS_URL", "INGESTION_QUEUE_NAME"))
            config = describe_config(env) if service == "api" else "web app (no database settings)"
            found.append(f"port {port} ({service}) is held by pid {holder}: {command_of(holder)}\n"
                         f"       it runs with: {config}\n       stop it yourself with: kill {holder}")
    if "worker" in roles:
        for pid, group, env in find_workers():
            description = describe_config(env)
            if env.get("INGESTION_QUEUE_NAME") == EXPECTED_QUEUE:
                if group in mine and not include_ours:
                    continue
                found.append(f"a worker already serves queue {EXPECTED_QUEUE}: pid {pid} ({description})\n       stop it with Ctrl-C in its terminal, or: kill {pid}")
            elif group not in mine:
                print(f"[note] a worker you started elsewhere is running (pid {pid}, {description}); it is left alone")
    return found


def spawn(name: str, cmd: list[str], env: dict[str, str], cwd: Path) -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    PIDS.mkdir(parents=True, exist_ok=True)
    log = open(LOGS / f"{name}.log", "wb")
    os.chmod(LOGS / f"{name}.log", 0o600)
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    (PIDS / f"{name}.pid").write_text(f"{proc.pid}\n{proc_start(proc.pid)}\n")
    return proc.pid


def http_get(url: str, timeout: int = 5) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError):
        return 0, ""


def wait_for(url: str, ok_codes: tuple[int, ...], seconds: int) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if http_get(url)[0] in ok_codes:
            return True
        time.sleep(1)
    return False


def start_services(merged: dict[str, str]) -> None:
    parent = dict(os.environ)
    shared = service_env(merged, parent)
    for name, cmd, env, cwd in (
        ("api", [str(PYTHON), "-m", "uvicorn", "apps.api.main:app", "--host", "127.0.0.1", "--port", str(API_PORT)], shared, ROOT),
        ("worker", [str(PYTHON), "-m", "apps.api.worker"], shared, ROOT),
        ("web", ["npm", "run", "dev", "--", "-p", str(WEB_PORT)], web_env(parent), ROOT / "apps/web"),
    ):
        pid = read_pid(name)
        if pid and is_ours(name):
            print(f"[ ok ] {name:<18} already running (pid {pid})")
            continue
        print(f"[ ok ] {name:<18} started (pid {spawn(name, cmd, env, cwd)}), log .dev/logs/{name}.log")


def verify(merged: dict[str, str], offline: bool, wait: bool) -> Report:
    report = Report()
    print("\nVerification (free checks only; no migration, no queue change, no paid provider call)")
    if wait:
        wait_for(f"{API_URL}/health/ready", (200,), 60)
        wait_for(f"{WEB_URL}/", (200, 307), 120)

    # processes and the configuration each one really runs with. Found by port / command, so this works for the
    # all-in-one stack and for services started separately in their own terminals.
    def storage_ok(env: dict[str, str]) -> bool:
        return (_db_name(env.get("DATABASE_URL", "")) == EXPECTED_DB and _redis_db(env.get("REDIS_URL", "")) == EXPECTED_REDIS_DB
                and env.get("INGESTION_QUEUE_NAME") == EXPECTED_QUEUE)

    api_pids = listeners(API_PORT)
    if api_pids:
        env = pick_env(ps_environment(api_pids[0]), ("DATABASE_URL", "REDIS_URL", "INGESTION_QUEUE_NAME", "GEMINI_API_KEY"))
        (report.ok if storage_ok(env) else report.fail)("api process", f"pid {api_pids[0]}; {describe_config(env)}; Gemini key present: {bool(env.get('GEMINI_API_KEY'))}")
    else:
        report.fail("api process", f"nothing listens on port {API_PORT} (run ./scripts/dev-api.sh)")
    ours_here = [w for w in find_workers() if w[2].get("INGESTION_QUEUE_NAME") == EXPECTED_QUEUE]
    wrong = [w for w in find_workers() if w[2].get("INGESTION_QUEUE_NAME") != EXPECTED_QUEUE]
    if ours_here:
        pid, _, env = ours_here[0]
        (report.ok if storage_ok(env) else report.fail)("worker process", f"pid {pid}; {describe_config(env)}; Gemini key present: {bool(env.get('GEMINI_API_KEY'))}")
        if len(ours_here) > 1:
            report.fail("duplicate workers", f"{len(ours_here)} workers serve queue {EXPECTED_QUEUE}; stop all but one")
    else:
        report.fail("worker process", f"no worker serves queue {EXPECTED_QUEUE} (run ./scripts/dev-worker.sh)")
    for pid, _, env in wrong:
        report.warn("other worker (left alone)", f"pid {pid}; {describe_config(env)}")
    web_pids = listeners(WEB_PORT)
    if web_pids:
        how = "started with only NEXT_PUBLIC_API_URL" if is_ours("web") else "its API URL cannot be inspected (Node hides its environment)"
        report.ok("web process", f"pid {web_pids[0]} listening on port {WEB_PORT}; {how}")
    else:
        report.fail("web process", f"nothing listens on port {WEB_PORT} (run ./scripts/dev-web.sh)")

    # API readiness
    status, body = http_get(f"{API_URL}/health/ready")
    (report.ok if status == 200 else report.fail)("API readiness", body.strip()[:80] if body else "no response")

    # worker registered on the isolated queue
    try:
        import redis

        client = redis.Redis.from_url(merged["REDIS_URL"], socket_connect_timeout=3)
        deadline = time.time() + (20 if wait else 1)
        count = client.scard(f"rq:workers:{EXPECTED_QUEUE}")
        while not count and time.time() < deadline:
            time.sleep(1)
            count = client.scard(f"rq:workers:{EXPECTED_QUEUE}")
        (report.ok if count else report.fail)("worker registered in Redis", f"{count} worker(s) on queue {EXPECTED_QUEUE} (db {EXPECTED_REDIS_DB})")
    except Exception as exc:  # noqa: BLE001
        report.fail("worker registered in Redis", f"cannot read Redis ({type(exc).__name__})")

    # GitHub login configuration, as the browser would see it
    status, body = http_get(f"{API_URL}/auth/me")
    try:
        enabled = json.loads(body).get("github_login_enabled") is True
    except ValueError:
        enabled = False
    (report.ok if enabled else report.fail)("GitHub login enabled", f"/auth/me github_login_enabled={enabled}")
    try:
        conn = http.client.HTTPConnection("localhost", API_PORT, timeout=8)
        conn.request("GET", "/auth/github/login?next=%2Frepositories")
        response = conn.getresponse()
        response.read()
        location = urlsplit(response.getheader("Location") or "")
        query = dict(part.split("=", 1) for part in location.query.split("&") if "=" in part)
        good = (response.status == 303 and location.netloc == "github.com" and location.path == "/login/oauth/authorize"
                and query.get("client_id") == merged.get("GITHUB_OAUTH_CLIENT_ID") and unquote(query.get("redirect_uri", "")) == CALLBACK
                and query.get("code_challenge_method") == "S256" and "scope" not in query)
        (report.ok if good else report.fail)("OAuth redirect", f"{response.status} to {location.netloc}{location.path}; callback {CALLBACK}; PKCE S256; no scope; client id matches")
    except OSError as exc:
        report.fail("OAuth redirect", f"API not reachable ({type(exc).__name__})")

    # the credential pair, checked by GitHub itself: 404 for a dummy token = valid pair, 401 = rejected
    if offline:
        report.warn("GitHub credential check", "skipped (--offline)")
    else:
        cid, secret = merged.get("GITHUB_OAUTH_CLIENT_ID", ""), merged.get("GITHUB_OAUTH_CLIENT_SECRET", "")
        token = base64.b64encode(f"{cid}:{secret}".encode()).decode()
        request = urllib.request.Request(
            f"https://api.github.com/applications/{cid}/token", method="POST", data=b'{"access_token":"gho_notarealtoken000000000000000000000"}',
            headers={"Authorization": f"Basic {token}", "Accept": "application/vnd.github+json", "User-Agent": "repolens-dev", "Content-Type": "application/json"},
        )
        try:
            code = urllib.request.urlopen(request, timeout=10).status
        except urllib.error.HTTPError as exc:
            code = exc.code
        except (urllib.error.URLError, OSError):
            code = 0
        if code in (200, 404, 422):
            report.ok("GitHub credential check", "GitHub accepts the client id and secret")
        elif code == 401:
            report.fail("GitHub credential check", "GitHub REJECTS this client id/secret pair; re-copy them from the OAuth App")
        else:
            report.warn("GitHub credential check", f"could not be completed (HTTP {code or 'no response'})")

    # repositories as a visitor (the guest showcase)
    status, body = http_get(f"{API_URL}/repositories")
    try:
        items = json.loads(body) if status == 200 else None
    except ValueError:
        items = None
    (report.ok if isinstance(items, list) else report.fail)("repository list (visitor)", f"HTTP {status}; {len(items) if isinstance(items, list) else '?'} showcase repositor{'y' if isinstance(items, list) and len(items) == 1 else 'ies'}")

    # the web app
    status, _ = http_get(f"{WEB_URL}/repositories")
    (report.ok if status == 200 else report.fail)("web app", f"{WEB_URL}/repositories -> HTTP {status}")

    # AI providers as the app resolves them from the merged environment (objects only: no network call)
    code = (
        "from apps.api.config import get_settings\n"
        "from apps.api.services.generation import get_generator\n"
        "from apps.api.services.embeddings import get_embedder\n"
        "s=get_settings()\n"
        "model=s.groq_generation_model if s.generation_provider=='groq' else s.gemini_generation_model\n"
        "print('provider',s.generation_provider,model)\n"
        "print('generator',get_generator() is not None)\n"
        "print('embedder',get_embedder() is not None,s.embedding_model,s.embedding_dimensions)\n"
    )
    out = run([str(PYTHON), "-c", code], env=service_env(merged, dict(os.environ)), timeout=60)
    lines = {l.split()[0]: l.split()[1:] for l in out.stdout.splitlines() if l.strip()}
    if out.returncode == 0 and lines.get("generator") == ["True"] and lines.get("embedder", [""])[0] == "True":
        report.ok("AI providers", f"answers: {' '.join(lines['provider'])}; embeddings: {lines['embedder'][1]} at {lines['embedder'][2]} dimensions")
    else:
        report.fail("AI providers", "generator or embedder not configured from the merged environment")

    print(f"\n{'All checks passed.' if not report.failures else str(report.failures) + ' check(s) FAILED.'}")
    return report


# --------------------------------------------------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------------------------------------------------

def cmd_check(args: argparse.Namespace) -> int:
    merged, overlay = load_or_exit()
    return 0 if preflight(merged, overlay) else 1


def cmd_start(args: argparse.Namespace) -> int:
    merged, overlay = load_or_exit()
    if not preflight(merged, overlay):
        return 1
    env = service_env(merged, dict(os.environ))
    if not docker_up(env) or not database_ready(merged, env):
        return 1
    problems = conflicts(("api", "worker", "web"))
    if problems:
        print("\n[FAIL] Cannot start: something else is already using these resources. Nothing was started or stopped.")
        for p in problems:
            print(f"  - {p}")
        return 1
    start_services(merged)
    return 1 if verify(merged, args.offline, wait=True).failures else 0


def cmd_status(args: argparse.Namespace) -> int:
    merged, _ = load_or_exit()
    return 1 if verify(merged, args.offline, wait=False).failures else 0


def cmd_stop(args: argparse.Namespace) -> int:
    for name in reversed(SERVICES):
        pid = read_pid(name)
        pidfile = PIDS / f"{name}.pid"
        if pid is None:
            print(f"[ ok ] {name:<8} not running")
            continue
        if not is_ours(name):
            pidfile.unlink(missing_ok=True)
            print(f"[ ok ] {name:<8} not running (stale pid file removed; a process that is not ours was left alone)")
            continue
        pgid = pgid_of(pid) or pid
        os.killpg(pgid, signal.SIGTERM)
        deadline = time.time() + TERM_WAIT_SECONDS
        while time.time() < deadline and pid_alive(pid):
            time.sleep(0.5)
        if pid_alive(pid):
            if args.force:
                os.killpg(pgid, signal.SIGKILL)
                time.sleep(1)
                print(f"[warn] {name:<8} did not stop in {TERM_WAIT_SECONDS}s and was killed (--force)")
            else:
                print(f"[FAIL] {name:<8} is still finishing work after {TERM_WAIT_SECONDS}s (a worker waits for its current job). Run again, or add --force")
                continue
        else:
            print(f"[ ok ] {name:<8} stopped")
        pidfile.unlink(missing_ok=True)
    if args.docker:
        out = run(["docker", "compose", "stop"], timeout=120)
        print("[ ok ] docker   containers stopped (volumes and data preserved)" if out.returncode == 0 else f"[FAIL] docker compose stop: {out.stderr.strip()[-200:]}")
    else:
        print("       Docker (PostgreSQL, Redis) left running; add --docker to stop the containers (data is kept).")
    return 0


def docker_running(env: dict[str, str]) -> bool:
    out = run(["docker", "compose", "ps", "--status", "running", "--format", "{{.Service}}"], env=env, timeout=30)
    return {"postgres", "redis"} <= set(out.stdout.split())


def prepare(role: str) -> tuple[dict[str, str], dict[str, str]] | None:
    """Every guard for starting one API or worker in the foreground. Returns (merged config, child environment), or
    None after printing why it refused. Read-only: nothing is started, created, migrated or stopped."""
    merged, overlay = load_or_exit()
    if not preflight(merged, overlay):
        return None
    env = service_env(merged, dict(os.environ))
    if not docker_running(env):
        print("[FAIL] PostgreSQL and Redis are not running. Start them first: ./scripts/dev-docker.sh\nNothing was started.")
        return None
    if not database_ready(merged, env):
        return None
    problems = conflicts((role,), include_ours=True)
    if problems:
        print(f"\n[FAIL] Cannot start the {role}: it is already running or its resources are in use. Nothing was started or stopped.")
        for problem in problems:
            print(f"  - {problem}")
        return None
    return merged, env


def run_in_foreground(role: str, merged: dict[str, str], cmd: list[str], env: dict[str, str], cwd: Path) -> int:
    """Replace this process with the real one, so its logs stay in your terminal and Ctrl-C stops it normally."""
    print(banner(role, merged))
    print(f"  command      {' '.join(cmd[1:]) if cmd[0] == str(PYTHON) else ' '.join(cmd)}\n", flush=True)
    os.chdir(cwd)
    os.execvpe(cmd[0], cmd, env)
    return 1  # unreachable


def cmd_api(args: argparse.Namespace) -> int:
    ready = prepare("api")
    if ready is None:
        return 1
    merged, env = ready
    cmd = [str(PYTHON), "-m", "uvicorn", "apps.api.main:app", "--host", "127.0.0.1", "--port", str(API_PORT)]
    if args.reload:
        cmd += ["--reload", "--reload-dir", "apps", "--reload-dir", "database"]
    return run_in_foreground("api", merged, cmd, env, ROOT)


def cmd_worker(args: argparse.Namespace) -> int:
    ready = prepare("worker")
    if ready is None:
        return 1
    merged, env = ready
    return run_in_foreground("worker", merged, [str(PYTHON), "-m", "apps.api.worker"], env, ROOT)


def cmd_web(args: argparse.Namespace) -> int:
    problems = conflicts(("web",), include_ours=True)
    if problems:
        print("[FAIL] Cannot start the web app. Nothing was started or stopped.")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"[RepoLens dev] web\n  API url      {API_URL} (the only setting it receives; it holds no secrets)\n  command      npm run dev -- -p {WEB_PORT}\n", flush=True)
    os.chdir(ROOT / "apps/web")
    cmd = ["npm", "run", "dev", "--", "-p", str(WEB_PORT)]
    os.execvpe("npm", cmd, web_env(dict(os.environ)))
    return 1  # unreachable


def cmd_docker(args: argparse.Namespace) -> int:
    merged, overlay = load_or_exit()
    if not preflight(merged, overlay):
        return 1
    env = service_env(merged, dict(os.environ))
    if not docker_up(env) or not database_ready(merged, env):
        return 1
    if args.detach:
        return 0
    print("\nFollowing PostgreSQL and Redis logs. Ctrl-C only stops the log view; the containers keep running.\n", flush=True)
    os.execvpe("docker", ["docker", "compose", "logs", "-f", "--tail", "20"], env)
    return 1  # unreachable


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn in (("start", cmd_start), ("status", cmd_status), ("check", cmd_check)):
        p = sub.add_parser(name)
        p.add_argument("--offline", action="store_true", help="skip the GitHub credential check (needs the network)")
        p.set_defaults(fn=fn)
    p = sub.add_parser("docker")
    p.add_argument("--detach", action="store_true", help="start the containers, check the database, and exit (no log view)")
    p.set_defaults(fn=cmd_docker)
    p = sub.add_parser("api")
    p.add_argument("--reload", action="store_true", help="restart the API when code changes (uvicorn --reload)")
    p.set_defaults(fn=cmd_api)
    sub.add_parser("worker").set_defaults(fn=cmd_worker)
    sub.add_parser("web").set_defaults(fn=cmd_web)
    p = sub.add_parser("stop")
    p.add_argument("--docker", action="store_true", help="also stop the PostgreSQL and Redis containers (data is kept)")
    p.add_argument("--force", action="store_true", help="SIGKILL a process that ignores SIGTERM")
    p.set_defaults(fn=cmd_stop)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
