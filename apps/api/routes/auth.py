"""GitHub sign-in, session and CSRF endpoints (see docs/auth-design.md)."""

import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import Settings
from apps.api.core.auth import Principal, get_principal, github_account_of, require_csrf, settings_dep
from apps.api.core.database import get_db
from apps.api.core.redis import get_redis
from apps.api.models.entities import GitHubAccount, User
from apps.api.services import github_oauth as oauth
from apps.api.services.sessions import create_session, purge_dead_sessions, revoke_all_sessions, revoke_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

_STATE_KEY = "rl:oauth:state:"
_SAFE_PATH = re.compile(r"^/[A-Za-z0-9\-._~!$&'()*+,;=:@%/?#\[\]]*$")


def safe_next(value: str | None) -> str:
    """A same-app relative path to return to after sign-in; anything else becomes the repositories page."""
    default = "/repositories"
    if not value or len(value) > 2048 or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return default
    if not _SAFE_PATH.match(value) or urlsplit(value).netloc:
        return default
    return value


def _web_redirect(settings: Settings, path: str) -> RedirectResponse:
    return RedirectResponse(settings.web_base_url.rstrip("/") + path, status_code=303)


def _auth_error(settings: Settings, code: str) -> RedirectResponse:
    response = _web_redirect(settings, f"/repositories?auth_error={quote(code)}")
    response.delete_cookie(settings.oauth_state_cookie_name, path="/")
    return response


class UserOut(BaseModel):
    id: int
    login: str
    name: str | None
    avatar_url: str | None


class MeResponse(BaseModel):
    authenticated: bool
    user: UserOut | None = None
    csrf_token: str | None = None
    github_login_enabled: bool


@router.get("/github/login")
async def github_login(
    request: Request,
    next: str | None = None,
    settings: Settings = Depends(settings_dep),
    redis: Redis = Depends(get_redis),
    client: oauth.GitHubOAuthClient = Depends(oauth.get_oauth_client),
) -> RedirectResponse:
    """Start sign-in: store state + PKCE verifier server-side, bind the state to this browser, redirect to GitHub."""
    if not settings.github_login_enabled:
        raise HTTPException(status_code=503, detail="GitHub sign-in is not configured on this server")
    state, verifier = oauth.new_state(), oauth.new_verifier()
    try:
        await redis.set(_STATE_KEY + state, json.dumps({"verifier": verifier, "next": safe_next(next)}), ex=settings.oauth_state_ttl_seconds)
    except (RedisError, OSError) as exc:
        logger.warning("Could not store the OAuth state: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Sign-in is temporarily unavailable") from exc
    response = RedirectResponse(client.authorize_url(state, oauth.challenge_for(verifier)), status_code=303)
    response.set_cookie(
        settings.oauth_state_cookie_name, state, max_age=settings.oauth_state_ttl_seconds,
        httponly=True, secure=settings.cookie_secure, samesite="lax", path="/",
    )
    return response


async def _upsert_user(db: AsyncSession, identity: oauth.GitHubIdentity) -> User:
    now = datetime.now(timezone.utc)
    for attempt in (1, 2):
        account = (
            await db.execute(select(GitHubAccount).where(GitHubAccount.github_user_id == identity.github_user_id))
        ).scalar_one_or_none()
        try:
            if account is None:
                user = User(email=None)
                db.add(user)
                await db.flush()
                account = GitHubAccount(user_id=user.id, github_user_id=identity.github_user_id, login=identity.login)
                db.add(account)
            else:
                user = (await db.execute(select(User).where(User.id == account.user_id))).scalar_one()
            account.login, account.display_name, account.avatar_url = identity.login, identity.display_name, identity.avatar_url
            account.last_login_at = now
            await db.flush()
            return user
        except IntegrityError:  # two first sign-ins of the same GitHub user raced; the other one won
            await db.rollback()
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


@router.get("/github/callback")
async def github_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    redis: Redis = Depends(get_redis),
    client: oauth.GitHubOAuthClient = Depends(oauth.get_oauth_client),
) -> Response:
    cookie_state = request.cookies.get(settings.oauth_state_cookie_name)
    if not state or not cookie_state or len(state) > 128 or state != cookie_state:
        return _auth_error(settings, "invalid_state")
    try:
        raw = await redis.getdel(_STATE_KEY + state)  # single use
    except (RedisError, OSError):
        return _auth_error(settings, "github_unavailable")
    if raw is None:
        return _auth_error(settings, "invalid_state")
    stored = json.loads(raw)
    if error:  # the user pressed Cancel, or GitHub refused: nothing is created
        return _auth_error(settings, "access_denied" if error == "access_denied" else "oauth_failed")
    if not code or len(code) > 512:
        return _auth_error(settings, "oauth_failed")
    try:
        identity = await client.identity_for_code(code, stored["verifier"])
    except oauth.OAuthError as exc:
        return _auth_error(settings, exc.code)

    user = await _upsert_user(db, identity)
    await purge_dead_sessions(db)
    token, _session = await create_session(db, user.id, settings, request.headers.get("user-agent"))
    await db.commit()
    response = _web_redirect(settings, safe_next(stored.get("next")))
    response.delete_cookie(settings.oauth_state_cookie_name, path="/")
    response.set_cookie(
        settings.session_cookie_name, token, max_age=settings.session_ttl_seconds,
        httponly=True, secure=settings.cookie_secure, samesite="lax", path="/",
    )
    return response


@router.get("/me", response_model=MeResponse)
async def me(
    response: Response,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> MeResponse:
    response.headers["Cache-Control"] = "no-store"
    if principal.user is None or principal.session is None:
        return MeResponse(authenticated=False, github_login_enabled=settings.github_login_enabled)
    account = await github_account_of(db, principal.user.id)
    if account is None:  # the local development user has no GitHub identity and cannot hold a session
        return MeResponse(authenticated=False, github_login_enabled=settings.github_login_enabled)
    return MeResponse(
        authenticated=True,
        user=UserOut(id=principal.user.id, login=account.login, name=account.display_name, avatar_url=account.avatar_url),
        csrf_token=principal.session.csrf_token,
        github_login_enabled=settings.github_login_enabled,
    )


@router.post("/logout", status_code=204)
async def logout(
    principal: Principal = Depends(require_csrf), db: AsyncSession = Depends(get_db), settings: Settings = Depends(settings_dep)
) -> Response:
    assert principal.session is not None
    await revoke_session(db, principal.session.id)
    response = Response(status_code=204)
    response.delete_cookie(settings.session_cookie_name, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax")
    return response


@router.post("/logout-all", status_code=204)
async def logout_everywhere(
    principal: Principal = Depends(require_csrf), db: AsyncSession = Depends(get_db), settings: Settings = Depends(settings_dep)
) -> Response:
    assert principal.user is not None
    await revoke_all_sessions(db, principal.user.id)
    response = Response(status_code=204)
    response.delete_cookie(settings.session_cookie_name, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax")
    return response
