"""GitHub sign-in, sessions, logout and CSRF, with a scripted GitHub identity (no network, no credentials)."""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.auth import settings_dep
from apps.api.core.redis import get_redis
from apps.api.main import app
from apps.api.models.entities import AuthSession, GitHubAccount, User
from apps.api.routes.auth import safe_next
from apps.api.services import github_oauth as oauth
from apps.api.services.sessions import hash_token
from tests.auth_support import WEB, FakeOAuth, FakeRedis, begin_login, identity, make_settings, query_of

pytestmark = pytest.mark.anyio


@pytest.fixture
def env(anon_client: httpx.AsyncClient):
    """The anonymous client wired to fake Redis, a scripted GitHub and explicit settings."""
    settings = make_settings()
    state = {"settings": settings, "redis": FakeRedis(), "oauth": FakeOAuth(settings, identity()), "client": anon_client}
    app.dependency_overrides[settings_dep] = lambda: state["settings"]
    app.dependency_overrides[get_redis] = lambda: state["redis"]
    app.dependency_overrides[oauth.get_oauth_client] = lambda: state["oauth"]
    yield state
    for dep in (settings_dep, get_redis, oauth.get_oauth_client):
        app.dependency_overrides.pop(dep, None)


async def sign_in(env: dict, next: str | None = None) -> httpx.Response:
    client = env["client"]
    state, _ = await begin_login(client, next)
    return await client.get("/auth/github/callback", params={"code": "good-code", "state": state})


async def count(db: AsyncSession, model) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


async def test_login_redirects_to_github_with_state_pkce_and_no_scope(env) -> None:
    state, response = await begin_login(env["client"], "/repositories?import=x")
    location = response.headers["location"]
    q = query_of(location)
    assert urlsplit(location).netloc == "github.com"
    assert q["client_id"] == ["test-client-id"] and q["redirect_uri"] == ["http://testserver/auth/github/callback"]
    assert q["code_challenge_method"] == ["S256"] and len(q["code_challenge"][0]) == 43
    assert "scope" not in q  # identity only: no repository access of any kind
    cookie = response.headers["set-cookie"].lower()
    assert f"rl_oauth={state}".lower() in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert len(state) >= 40 and ("rl:oauth:state:" + state) in env["redis"].data


async def test_successful_callback_creates_user_and_session(env, real_db_session: AsyncSession) -> None:
    users_before = await count(real_db_session, User)
    response = await sign_in(env, "/repositories?import=https%3A%2F%2Fgithub.com%2Fo%2Fr")
    assert response.status_code == 303
    assert response.headers["location"] == f"{WEB}/repositories?import=https%3A%2F%2Fgithub.com%2Fo%2Fr"
    cookie = response.headers["set-cookie"]
    assert "rl_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie and "Secure" not in cookie
    token = env["client"].cookies.get("rl_session")
    assert token and len(token) >= 40

    assert await count(real_db_session, User) == users_before + 1
    account = (await real_db_session.execute(select(GitHubAccount).where(GitHubAccount.github_user_id == 1001))).scalar_one()
    assert (account.login, account.display_name) == ("octocat", "The Octocat")
    assert account.avatar_url.startswith("https://avatars.githubusercontent.com/")
    row = (await real_db_session.execute(select(AuthSession).where(AuthSession.user_id == account.user_id))).scalar_one()
    assert row.token_hash == hash_token(token) and row.token_hash != token  # only the hash is stored
    assert row.expires_at > datetime.now(timezone.utc) + timedelta(days=13)
    assert env["oauth"].calls and env["oauth"].calls[0][0] == "good-code"

    me = (await env["client"].get("/auth/me")).json()
    assert me["authenticated"] is True and me["user"]["login"] == "octocat" and me["user"]["name"] == "The Octocat"
    assert me["csrf_token"] == row.csrf_token and me["github_login_enabled"] is True


async def test_signing_in_again_updates_the_same_user(env, real_db_session: AsyncSession) -> None:
    await sign_in(env)
    users = await count(real_db_session, User)
    env["oauth"].identity = identity(login="octocat-renamed", name="New Name")
    await sign_in(env)
    assert await count(real_db_session, User) == users  # keyed by the numeric GitHub id, not the login
    account = (await real_db_session.execute(select(GitHubAccount).where(GitHubAccount.github_user_id == 1001))).scalar_one()
    assert (account.login, account.display_name) == ("octocat-renamed", "New Name")
    assert await count(real_db_session, AuthSession) >= 2  # every sign-in gets a fresh session


@pytest.mark.parametrize("case", ["wrong_cookie", "no_cookie", "unknown_state", "reused", "no_state"])
async def test_invalid_state_is_rejected_and_creates_nothing(env, real_db_session: AsyncSession, case: str) -> None:
    client = env["client"]
    state, _ = await begin_login(client)
    users, sessions = await count(real_db_session, User), await count(real_db_session, AuthSession)
    params = {"code": "good-code", "state": state}
    if case == "wrong_cookie":
        client.cookies.set("rl_oauth", "something-else")
    elif case == "no_cookie":
        client.cookies.clear()
    elif case == "unknown_state":
        params["state"] = "forged"
        client.cookies.set("rl_oauth", "forged")
    elif case == "no_state":
        params.pop("state")
    elif case == "reused":
        assert (await client.get("/auth/github/callback", params=params)).headers["location"] == f"{WEB}/repositories"
        client.cookies.clear()
        client.cookies.set("rl_oauth", state)
        users, sessions = await count(real_db_session, User), await count(real_db_session, AuthSession)
    response = await client.get("/auth/github/callback", params=params)
    assert response.status_code == 303 and response.headers["location"] == f"{WEB}/repositories?auth_error=invalid_state"
    assert "rl_session" not in response.headers.get("set-cookie", "").split("rl_oauth")[0]
    assert (await count(real_db_session, User), await count(real_db_session, AuthSession)) == (users, sessions)


async def test_cancelling_on_github_creates_nothing(env, real_db_session: AsyncSession) -> None:
    client = env["client"]
    state, _ = await begin_login(client)
    users = await count(real_db_session, User)
    response = await client.get("/auth/github/callback", params={"error": "access_denied", "state": state})
    assert response.headers["location"] == f"{WEB}/repositories?auth_error=access_denied"
    assert env["oauth"].calls == [] and await count(real_db_session, User) == users
    assert (await client.get("/auth/me")).json()["authenticated"] is False


@pytest.mark.parametrize("code", ["github_unavailable", "oauth_failed"])
async def test_github_failures_are_reported_without_a_session(env, real_db_session: AsyncSession, code: str) -> None:
    env["oauth"].error = code
    users = await count(real_db_session, User)
    response = await sign_in(env)
    assert response.headers["location"] == f"{WEB}/repositories?auth_error={code}"
    assert await count(real_db_session, User) == users
    assert (await env["client"].get("/auth/me")).json()["authenticated"] is False


async def test_login_is_unavailable_without_oauth_credentials_or_redis(env) -> None:
    env["settings"] = make_settings(github_oauth_client_id=None)
    assert (await env["client"].get("/auth/github/login")).status_code == 503
    assert (await env["client"].get("/auth/me")).json()["github_login_enabled"] is False
    env["settings"] = make_settings()
    env["redis"].fail = True
    assert (await env["client"].get("/auth/github/login")).status_code == 503


@pytest.mark.parametrize(
    "value, expected",
    [
        ("/repositories?import=a", "/repositories?import=a"),
        ("/repos/3?tab=ask&c=4", "/repos/3?tab=ask&c=4"),
        ("//evil.example/x", "/repositories"),
        ("https://evil.example", "/repositories"),
        ("/\\evil.example", "/repositories"),
        ("javascript:alert(1)", "/repositories"),
        ("/ok\r\nSet-Cookie: a=b", "/repositories"),
        ("/" + "a" * 3000, "/repositories"),
        (None, "/repositories"),
        ("", "/repositories"),
    ],
)
def test_next_is_always_a_relative_path_of_this_app(value, expected) -> None:
    assert safe_next(value) == expected


async def test_open_redirect_attempt_stays_on_the_web_app(env) -> None:
    response = await sign_in(env, "//evil.example/steal")
    assert response.headers["location"] == f"{WEB}/repositories"


async def test_me_is_anonymous_without_a_session(env) -> None:
    body = (await env["client"].get("/auth/me")).json()
    assert body == {"authenticated": False, "user": None, "csrf_token": None, "github_login_enabled": True}


async def test_expired_idle_and_revoked_sessions_are_signed_out(env, real_db_session: AsyncSession) -> None:
    client = env["client"]
    await sign_in(env)
    assert (await client.get("/auth/me")).json()["authenticated"] is True
    row = (await real_db_session.execute(select(AuthSession))).scalars().first()

    now = datetime.now(timezone.utc)
    await real_db_session.execute(update(AuthSession).where(AuthSession.id == row.id).values(expires_at=now - timedelta(seconds=1)))
    assert (await client.get("/auth/me")).json()["authenticated"] is False  # past its absolute lifetime

    await real_db_session.execute(
        update(AuthSession).where(AuthSession.id == row.id).values(expires_at=now + timedelta(days=1), last_seen_at=now - timedelta(days=8))
    )
    assert (await client.get("/auth/me")).json()["authenticated"] is False  # idle for longer than 7 days

    await real_db_session.execute(update(AuthSession).where(AuthSession.id == row.id).values(last_seen_at=now, revoked_at=now))
    assert (await client.get("/auth/me")).json()["authenticated"] is False  # revoked


async def test_logout_revokes_the_session_and_needs_the_csrf_token(env, real_db_session: AsyncSession) -> None:
    client = env["client"]
    await sign_in(env)
    csrf = (await client.get("/auth/me")).json()["csrf_token"]

    assert (await client.post("/auth/logout")).status_code == 403  # no token: a forged cross-site POST fails
    assert (await client.post("/auth/logout", headers={"X-CSRF-Token": "wrong"})).status_code == 403
    assert (await client.post("/auth/logout", headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"})).status_code == 403
    assert (await client.get("/auth/me")).json()["authenticated"] is True

    stolen_cookie = client.cookies.get("rl_session")
    response = await client.post("/auth/logout", headers={"X-CSRF-Token": csrf, "Origin": WEB})
    assert response.status_code == 204 and "rl_session=" in response.headers["set-cookie"]
    row = (await real_db_session.execute(select(AuthSession))).scalars().first()
    assert row.revoked_at is not None
    client.cookies.set("rl_session", stolen_cookie)  # even a copied cookie is dead after logout
    assert (await client.get("/auth/me")).json()["authenticated"] is False


async def test_logout_all_revokes_every_session_of_the_user(env, real_db_session: AsyncSession) -> None:
    await sign_in(env)
    other_token = env["client"].cookies.get("rl_session")
    env["client"].cookies.clear()
    await sign_in(env)
    csrf = (await env["client"].get("/auth/me")).json()["csrf_token"]
    assert (await env["client"].post("/auth/logout-all", headers={"X-CSRF-Token": csrf})).status_code == 204
    env["client"].cookies.set("rl_session", other_token)
    assert (await env["client"].get("/auth/me")).json()["authenticated"] is False
    live = (await real_db_session.execute(select(func.count()).select_from(AuthSession).where(AuthSession.revoked_at.is_(None)))).scalar_one()
    assert live == 0


async def test_production_cookies_are_secure_and_host_prefixed(env) -> None:
    env["settings"] = make_settings(environment="production", web_base_url="https://app.example.com", web_origins="https://app.example.com")
    state, response = await begin_login(env["client"])
    assert "__Host-rl_oauth=" in response.headers["set-cookie"] and "Secure" in response.headers["set-cookie"]
    env["client"].cookies.clear()
    state, _ = await begin_login(env["client"])
    env["client"].cookies.set("__Host-rl_oauth", state)
    callback = await env["client"].get("/auth/github/callback", params={"code": "c", "state": state})
    cookie = callback.headers["set-cookie"]
    assert "__Host-rl_session=" in cookie and "Secure" in cookie and "HttpOnly" in cookie and "Domain" not in cookie and "Path=/" in cookie


# --- the GitHub client itself, against a scripted HTTP transport -------------------------------------------------

def _client(handler, settings=None) -> oauth.GitHubOAuthClient:
    return oauth.GitHubOAuthClient(settings or make_settings(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_github_client_exchanges_the_code_with_pkce_reads_the_profile_and_revokes_the_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_secret_token", "token_type": "bearer", "scope": ""})
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 42, "login": "someone", "name": None, "avatar_url": "https://avatars.githubusercontent.com/u/42"})
        return httpx.Response(204)

    result = await _client(handler).identity_for_code("the-code", "the-verifier")
    assert result == oauth.GitHubIdentity(42, "someone", None, "https://avatars.githubusercontent.com/u/42")
    exchange = seen[0]
    body = exchange.content.decode()
    assert '"code_verifier":"the-verifier"' in body.replace(" ", "") and '"code":"the-code"' in body.replace(" ", "")
    assert seen[1].headers["authorization"] == "Bearer gho_secret_token"
    assert seen[2].method == "DELETE" and "/applications/test-client-id/token" in str(seen[2].url)  # token revoked after use


@pytest.mark.parametrize(
    "token_response, profile_response, code",
    [
        (httpx.Response(200, json={"error": "bad_verification_code"}), None, "oauth_failed"),
        (httpx.Response(500), None, "oauth_failed"),
        (httpx.Response(200, json={"access_token": "t"}), httpx.Response(401), "github_unavailable"),
        (httpx.Response(200, json={"access_token": "t"}), httpx.Response(200, json={"id": "not-a-number", "login": "x"}), "github_unavailable"),
        (httpx.Response(200, json={"access_token": "t"}), httpx.Response(200, json={"id": 5, "login": "bad login!"}), "github_unavailable"),
    ],
)
async def test_github_client_failures_map_to_safe_codes(token_response, profile_response, code) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return token_response
        if request.url.path == "/user":
            return profile_response
        return httpx.Response(204)

    with pytest.raises(oauth.OAuthError) as caught:
        await _client(handler).identity_for_code("c", "v")
    assert caught.value.code == code


async def test_github_client_network_failure_is_a_safe_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom with secret details")

    with pytest.raises(oauth.OAuthError) as caught:
        await _client(handler).identity_for_code("c", "v")
    assert caught.value.code == "github_unavailable" and "secret" not in str(caught.value)


def test_only_github_avatar_hosts_are_kept() -> None:
    assert oauth.parse_identity({"id": 1, "login": "a", "avatar_url": "https://evil.example/a.png"}).avatar_url is None
    assert oauth.parse_identity({"id": 1, "login": "a", "avatar_url": "http://avatars.githubusercontent.com/a"}).avatar_url is None
    assert oauth.parse_identity({"id": 1, "login": "a", "avatar_url": "https://avatars.githubusercontent.com/a"}).avatar_url
