"""GitHub OAuth (authorization code + PKCE), used only to learn who the user is.

No scope is requested, so the token can read nothing but public profile data. It is used once for `GET /user`,
revoked right away, and never stored or logged.
"""

import base64
import hashlib
import logging
import re
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from urllib.parse import urlencode, urlparse

import httpx

from apps.api.config import Settings, get_settings

logger = logging.getLogger(__name__)

_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


class OAuthError(Exception):
    """Sign-in failed. `code` is a short, safe identifier for the web app (never provider text)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class GitHubIdentity:
    github_user_id: int
    login: str
    display_name: str | None
    avatar_url: str | None


def new_state() -> str:
    return secrets.token_urlsafe(32)


def new_verifier() -> str:
    return secrets.token_urlsafe(48)  # 64 characters, within RFC 7636's 43-128


def challenge_for(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def _clean(value: object, limit: int) -> str | None:
    return value.strip()[:limit] or None if isinstance(value, str) else None


def parse_identity(payload: object) -> GitHubIdentity:
    """Validate GitHub's `GET /user` body. The numeric id is the identity; everything else is display data."""
    if not isinstance(payload, dict):
        raise OAuthError("github_unavailable")
    user_id, login = payload.get("id"), payload.get("login")
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0 or not isinstance(login, str) or not _LOGIN.match(login):
        raise OAuthError("github_unavailable")
    avatar = _clean(payload.get("avatar_url"), 1024)
    if avatar is not None:
        parsed = urlparse(avatar)
        if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".githubusercontent.com"):
            avatar = None  # shown in an <img>; only accept GitHub's own avatar host
    return GitHubIdentity(github_user_id=user_id, login=login, display_name=_clean(payload.get("name"), 255), avatar_url=avatar)


class GitHubOAuthClient:
    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = client

    def authorize_url(self, state: str, code_challenge: str) -> str:
        s = self._settings
        query = {
            "client_id": s.github_oauth_client_id,
            "redirect_uri": s.github_oauth_redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "allow_signup": "true",
        }  # deliberately no `scope`
        return f"{s.github_oauth_authorize_url}?{urlencode(query)}"

    async def identity_for_code(self, code: str, code_verifier: str) -> GitHubIdentity:
        s = self._settings
        assert s.github_oauth_client_secret is not None
        client = self._client or httpx.AsyncClient(timeout=s.github_oauth_timeout_seconds)
        token: str | None = None
        try:
            try:
                response = await client.post(
                    s.github_oauth_token_url,
                    json={
                        "client_id": s.github_oauth_client_id,
                        "client_secret": s.github_oauth_client_secret.get_secret_value(),
                        "code": code,
                        "redirect_uri": s.github_oauth_redirect_uri,
                        "code_verifier": code_verifier,
                    },
                    headers={"Accept": "application/json"},
                )
                body = response.json() if response.status_code == 200 else {}
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("GitHub token exchange failed (%s)", type(exc).__name__)
                raise OAuthError("github_unavailable") from exc
            token = body.get("access_token") if isinstance(body, dict) else None
            if not isinstance(token, str) or not token:
                # e.g. bad_verification_code (expired or reused code); never echo GitHub's text
                raise OAuthError("oauth_failed")
            try:
                profile = await client.get(
                    f"{s.github_api_base_url.rstrip('/')}/user",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                if profile.status_code != 200:
                    raise OAuthError("github_unavailable")
                return parse_identity(profile.json())
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("GitHub user lookup failed (%s)", type(exc).__name__)
                raise OAuthError("github_unavailable") from exc
        finally:
            if token:
                await self._revoke(client, token)
            if self._client is None:
                await client.aclose()

    async def _revoke(self, client: httpx.AsyncClient, token: str) -> None:
        """Best effort: the token only reads public data, but there is no reason to leave it alive."""
        s = self._settings
        try:
            await client.request(
                "DELETE",
                f"{s.github_api_base_url.rstrip('/')}/applications/{s.github_oauth_client_id}/token",
                json={"access_token": token},
                auth=(s.github_oauth_client_id or "", s.github_oauth_client_secret.get_secret_value() if s.github_oauth_client_secret else ""),
                headers={"Accept": "application/vnd.github+json"},
            )
        except httpx.HTTPError:
            logger.info("Could not revoke the GitHub token (it expires on its own)")


async def get_oauth_client() -> AsyncIterator[GitHubOAuthClient]:
    yield GitHubOAuthClient()
