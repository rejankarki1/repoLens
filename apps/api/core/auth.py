"""Who is making the request. Identity comes only from the session cookie; nothing the client sends names a user."""

import hmac
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import Settings, get_settings
from apps.api.core.database import get_db
from apps.api.models.entities import AuthSession, GitHubAccount, User
from apps.api.services.sessions import resolve_session

CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def settings_dep() -> Settings:
    """The settings, as a dependency so tests can override them."""
    return get_settings()


@dataclass(frozen=True)
class Principal:
    """The caller: `user` and `session` are None for a guest."""

    user: User | None = None
    session: AuthSession | None = None

    @property
    def authenticated(self) -> bool:
        return self.user is not None


async def get_principal(
    request: Request, db: AsyncSession = Depends(get_db), settings: Settings = Depends(settings_dep)
) -> Principal:
    token = request.cookies.get(settings.session_cookie_name)
    session = await resolve_session(db, token, settings)
    if session is None:
        return Principal()
    user = (await db.execute(select(User).where(User.id == session.user_id))).scalar_one_or_none()
    return Principal(user=user, session=session) if user is not None else Principal()


async def require_user(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.authenticated:
        raise HTTPException(status_code=401, detail="Sign in with GitHub to continue")
    return principal


async def require_csrf(
    request: Request, principal: Principal = Depends(require_user), settings: Settings = Depends(settings_dep)
) -> Principal:
    """For state-changing requests: a signed-in user plus the per-session CSRF token (and, if sent, a known Origin)."""
    assert principal.session is not None
    sent = request.headers.get(CSRF_HEADER, "")
    if not sent or not hmac.compare_digest(sent.encode(), principal.session.csrf_token.encode()):
        raise HTTPException(status_code=403, detail="Missing or invalid CSRF token; reload the page and try again")
    origin = request.headers.get("origin")
    if origin and origin not in settings.allowed_origins:
        raise HTTPException(status_code=403, detail="This origin is not allowed")
    return principal


async def github_account_of(db: AsyncSession, user_id: int) -> GitHubAccount | None:
    return (await db.execute(select(GitHubAccount).where(GitHubAccount.user_id == user_id))).scalars().first()
