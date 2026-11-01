"""Fakes and helpers for authentication tests: no GitHub, no OAuth credentials, no real Redis."""

from urllib.parse import parse_qs, urlsplit

import httpx

from apps.api.config import Settings
from apps.api.services import github_oauth as oauth

WEB = "http://localhost:3000"


def make_settings(**overrides) -> Settings:
    values = dict(
        github_oauth_client_id="test-client-id",
        github_oauth_client_secret="test-client-secret",
        web_base_url=WEB,
        api_public_url="http://testserver",
        web_origins=WEB,
        gemini_api_key=None,
        groq_api_key=None,
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)  # never read the developer's .env


class FakeRedis:
    """The few Redis commands the OAuth state store uses."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.fail = False

    async def set(self, key, value, ex=None, **_):
        if self.fail:
            raise OSError("redis down")
        self.data[key] = value
        return True

    async def getdel(self, key):
        if self.fail:
            raise OSError("redis down")
        return self.data.pop(key, None)


class FakeOAuth(oauth.GitHubOAuthClient):
    """Skips the network: returns a scripted identity (or raises a scripted OAuthError)."""

    def __init__(self, settings: Settings, identity: oauth.GitHubIdentity | None = None, error: str | None = None) -> None:
        super().__init__(settings)
        self.identity, self.error, self.calls = identity, error, []

    async def identity_for_code(self, code: str, code_verifier: str) -> oauth.GitHubIdentity:
        self.calls.append((code, code_verifier))
        if self.error:
            raise oauth.OAuthError(self.error)
        assert self.identity is not None
        return self.identity


def identity(github_id: int = 1001, login: str = "octocat", name: str | None = "The Octocat") -> oauth.GitHubIdentity:
    return oauth.GitHubIdentity(github_id, login, name, "https://avatars.githubusercontent.com/u/1?v=4")


def query_of(location: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(location).query)


async def begin_login(client: httpx.AsyncClient, next: str | None = None) -> tuple[str, httpx.Response]:
    """GET /auth/github/login without following the redirect. Returns the state and the response."""
    response = await client.get("/auth/github/login", params={"next": next} if next else None)
    assert response.status_code == 303, response.text
    return query_of(response.headers["location"])["state"][0], response
