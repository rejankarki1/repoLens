"""Hardening: headers, CORS, production start-up checks, log redaction, error bodies, input validation."""

import logging
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import get_settings
from apps.api.core.database import get_db
from apps.api.core.security import (
    RedactingFilter,
    SecurityHeadersMiddleware,
    install_log_redaction,
    production_problems,
    redact,
    validate_production_settings,
)
from apps.api.main import app
from apps.api.services.github import InvalidRepositoryURLError, is_valid_owner_repo, parse_github_url
from tests.auth_support import make_settings
from tests.search_support import ingest, new_repository

pytestmark = pytest.mark.anyio
ROOT = Path(__file__).resolve().parent.parent

PRODUCTION = dict(
    environment="production",
    web_base_url="https://app.example.com",
    api_public_url="https://api.example.com",
    web_origins="https://app.example.com",
    database_url="postgresql+asyncpg://repolens:a-long-random-password@db.internal:5432/repolens",
)


# --- headers and CORS -------------------------------------------------------------------------------------------

async def test_api_responses_carry_conservative_security_headers(anon_client: httpx.AsyncClient) -> None:
    response = await anon_client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "strict-transport-security" not in response.headers  # plain http in development
    assert (await anon_client.get("/auth/me")).headers["cache-control"] == "no-store"


async def test_production_adds_hsts_and_a_locked_down_csp() -> None:
    inner = FastAPI()

    @inner.get("/ping")
    async def ping() -> dict:
        return {}

    wrapped = SecurityHeadersMiddleware(inner, make_settings(**PRODUCTION))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=wrapped), base_url="https://api.example.com") as client:
        headers = (await client.get("/ping")).headers
    assert headers["strict-transport-security"].startswith("max-age=31536000")
    assert "default-src 'none'" in headers["content-security-policy"] and "frame-ancestors 'none'" in headers["content-security-policy"]


async def test_cors_allows_only_configured_origins_with_credentials(anon_client: httpx.AsyncClient) -> None:
    allowed = get_settings().allowed_origins[0]
    ok = await anon_client.options(
        "/repositories",
        headers={"Origin": allowed, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,x-csrf-token"},
    )
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == allowed
    assert ok.headers["access-control-allow-credentials"] == "true"
    assert "x-csrf-token" in ok.headers["access-control-allow-headers"].lower()
    assert "*" not in ok.headers["access-control-allow-origin"]

    evil = await anon_client.options(
        "/repositories", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
    )
    assert "access-control-allow-origin" not in evil.headers

    bad_method = await anon_client.options(
        "/repositories", headers={"Origin": allowed, "Access-Control-Request-Method": "PUT"}
    )
    assert bad_method.status_code == 400  # PUT is not an API method
    bad_header = await anon_client.options(
        "/repositories", headers={"Origin": allowed, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "x-evil"}
    )
    assert bad_header.status_code == 400

    simple = await anon_client.get("/repositories", headers={"Origin": allowed})
    exposed = simple.headers.get("access-control-expose-headers", "").lower()
    assert "retry-after" in exposed and "x-ratelimit-scope" in exposed  # the web app needs these to explain a 429


async def test_an_unhandled_exception_still_carries_cors_headers(anon_client: httpx.AsyncClient) -> None:
    """Starlette's default `ServerErrorMiddleware` sits outside `CORSMiddleware` (and `Exception`/500
    exception handlers are routed there too — see `Starlette.build_middleware_stack`), so a truly unhandled
    exception's 500 would otherwise carry no CORS headers: the browser blocks it, and `fetch()` throws a
    generic network error instead of surfacing the real failure (what actually happened when a repository's
    diagram table was missing). `UnhandledErrorMiddleware` (registered before `CORSMiddleware` in
    `main.py`, so it ends up inside it) must keep catching this so it can never regress silently."""
    async def boom() -> AsyncIterator[AsyncSession]:
        raise RuntimeError("simulated unhandled failure")
        yield  # pragma: no cover - makes this an async generator, matching get_db's shape

    app.dependency_overrides[get_db] = boom
    try:
        allowed = get_settings().allowed_origins[0]
        response = await anon_client.get("/repositories", headers={"Origin": allowed})
        assert response.status_code == 500
        assert response.headers.get("access-control-allow-origin") == allowed
        assert response.json() == {"detail": "Internal server error"}
    finally:
        app.dependency_overrides.pop(get_db, None)


# --- production configuration ------------------------------------------------------------------------------------

def test_a_correct_production_configuration_passes() -> None:
    assert production_problems(make_settings(**PRODUCTION)) == []
    validate_production_settings(make_settings(**PRODUCTION))
    validate_production_settings(make_settings())  # development is never blocked


@pytest.mark.parametrize(
    "override, needle",
    [
        ({"session_cookie_secure": False}, "Secure"),
        ({"web_base_url": "http://app.example.com"}, "WEB_BASE_URL"),
        ({"api_public_url": "http://api.example.com"}, "API_PUBLIC_URL"),
        ({"web_origins": "*"}, "WEB_ORIGINS"),
        ({"web_origins": "http://app.example.com"}, "WEB_ORIGINS"),
        ({"web_origins": "https://other.example.com"}, "must be one of WEB_ORIGINS"),
        ({"github_oauth_client_secret": None}, "GITHUB_OAUTH"),
        ({"database_url": "postgresql+asyncpg://repolens:local_dev_password@db/repolens"}, "development password"),
    ],
)
def test_unsafe_production_settings_are_each_refused(override, needle) -> None:
    settings = make_settings(**{**PRODUCTION, **override})
    assert any(needle in p for p in production_problems(settings))
    with pytest.raises(RuntimeError, match="Unsafe production configuration"):
        validate_production_settings(settings)


def test_production_start_up_is_wired_to_the_check_and_hides_the_docs() -> None:
    env = {**os.environ, "ENVIRONMENT": "production", "WEB_ORIGINS": "http://localhost:3000"}
    code = (
        "import asyncio\n"
        "from apps.api.main import app\n"
        "print('docs', app.docs_url, app.redoc_url, app.openapi_url)\n"
        "async def go():\n"
        "    async with app.router.lifespan_context(app): pass\n"
        "try:\n"
        "    asyncio.run(go())\n"
        "except RuntimeError as e:\n"
        "    print('refused', str(e).splitlines()[0])\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert "docs None None None" in out.stdout and "refused Unsafe production configuration" in out.stdout, out.stderr[-800:]


# --- logs and error bodies ---------------------------------------------------------------------------------------

def test_oauth_codes_state_and_tokens_are_redacted_from_logs() -> None:
    line = 'GET /auth/github/callback?code=ABC123&state=XYZ789 HTTP/1.1'
    assert redact(line) == "GET /auth/github/callback?code=[redacted]&state=[redacted] HTTP/1.1"
    hidden = redact("https://x/user?access_token=s3cr3tvalue&per_page=5")
    assert "s3cr3tvalue" not in hidden and "per_page=5" in hidden
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', ("1.2.3.4", "GET", "/auth/github/callback?code=ABC&state=XYZ", "1.1", 303), None)
    assert RedactingFilter().filter(record) and "ABC" not in record.getMessage() and "XYZ" not in record.getMessage()
    assert "303" in record.getMessage()


def test_log_redaction_is_installed_on_the_access_log_once() -> None:
    install_log_redaction()
    install_log_redaction()
    assert sum(isinstance(f, RedactingFilter) for f in logging.getLogger("uvicorn.access").filters) == 1


async def test_unexpected_errors_do_not_leak_internals(app_overrides) -> None:
    async def broken_db():
        raise RuntimeError("postgresql://repolens:SECRET-PASSWORD@internal-host/db failed")
        yield  # pragma: no cover

    app.dependency_overrides[get_db] = broken_db
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/repositories")
    assert response.status_code == 500
    assert "SECRET" not in response.text and "internal-host" not in response.text and "Traceback" not in response.text


async def test_auth_failures_say_nothing_about_internals(anon_client: httpx.AsyncClient) -> None:
    for response in (await anon_client.get("/repositories/1"), await anon_client.post("/repositories", json={"github_url": "https://github.com/a/b"})):
        assert response.status_code == 401 and set(response.json()) == {"detail"}


# --- input validation --------------------------------------------------------------------------------------------

async def test_nul_characters_are_rejected_instead_of_reaching_the_database(real_client, real_db_session) -> None:
    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id)
    nul = "a\x00b"
    for path, params in (
        (f"/repositories/{repo_id}/files/content", {"path": nul}),
        (f"/repositories/{repo_id}/architecture/nodes", {"id": "file:" + nul}),
        (f"/repositories/{repo_id}/search", {"q": nul}),
    ):
        assert (await real_client.get(path, params=params)).status_code == 422, path
    conv = (await real_client.post(f"/repositories/{repo_id}/conversations")).json()["id"]
    base = f"/repositories/{repo_id}/conversations/{conv}"
    assert (await real_client.patch(base, json={"title": nul})).status_code == 422
    assert (await real_client.post(base + "/messages", json={"content": nul})).status_code == 422
    assert (await real_client.post(f"/repositories/{repo_id}/conversations", json={"title": nul})).status_code == 422
    assert (await real_client.post("/repositories", json={"github_url": "https://github.com/a/b" + nul})).status_code == 422


async def test_path_parameters_are_data_never_filesystem_paths(real_client, real_db_session) -> None:
    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id)
    for path in ("../../etc/passwd", "/etc/passwd", "..\\..\\windows", "%2e%2e/%2e%2e/secret", "a/../../b"):
        assert (await real_client.get(f"/repositories/{repo_id}/files/content", params={"path": path})).status_code == 404
    assert (await real_client.get(f"/repositories/{repo_id}/files/content", params={"path": "x" * 5000})).status_code == 422


@pytest.mark.parametrize(
    "owner, repo, valid",
    [
        ("octocat", "Hello-World", True),
        ("a", "b", True),
        ("my-org", "repo.name_1", True),
        ("..", "repo", False),
        (".", "repo", False),
        ("octocat", "..", False),
        ("octocat", ".", False),
        ("octo/cat", "repo", False),
        ("-leading", "repo", False),
        ("under_score", "repo", False),
        ("o" * 40, "repo", False),
        ("octocat", "r" * 101, False),
        ("octocat", "re po", False),
        ("octocat", "", False),
    ],
)
def test_owner_and_repository_names_follow_githubs_rules(owner: str, repo: str, valid: bool) -> None:
    assert is_valid_owner_repo(owner, repo) is valid


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com.evil.com/octocat/Hello-World",
        "https://evil.com/github.com/octocat/Hello-World",
        "https://api.github.com/repos/octocat/Hello-World",
        "https://github.com/octocat/Hello-World/tree/main",
        "https://github.com/../etc",
        "ftp://github.com/octocat/Hello-World",
        "https://user:pass@github.com/octocat/Hello-World",
        "https://github.com:8443/octocat/Hello-World",
        "https://github.com:notaport/octocat/Hello-World",
        "file:///etc/passwd",
        "https://github.com/octocat/..",
    ],
)
def test_only_plain_github_repository_urls_are_accepted(url: str) -> None:
    with pytest.raises(InvalidRepositoryURLError):
        parse_github_url(url)
