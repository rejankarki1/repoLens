"""Server-side sessions. The cookie carries a random token; the database keeps only its SHA-256."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import Settings
from apps.api.models.entities import AuthSession


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_token() -> str:
    return secrets.token_urlsafe(32)  # 256 bits


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_session(db: AsyncSession, user_id: int, settings: Settings, user_agent: str | None = None) -> tuple[str, AuthSession]:
    """A new session for the user. Returns the raw cookie value (shown once) and the stored row."""
    token = new_token()
    now = _now()
    row = AuthSession(
        user_id=user_id,
        token_hash=hash_token(token),
        csrf_token=new_token(),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(seconds=settings.session_ttl_seconds),
        user_agent=(user_agent or "")[:255] or None,
    )
    db.add(row)
    await db.flush()
    return token, row


async def resolve_session(db: AsyncSession, token: str | None, settings: Settings) -> AuthSession | None:
    """The live session for a cookie value, or None (unknown, revoked, expired or idle for too long)."""
    if not token or len(token) > 128:
        return None
    row = (await db.execute(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    now = _now()
    if row.expires_at <= now or row.last_seen_at + timedelta(seconds=settings.session_idle_seconds) <= now:
        return None
    if now - row.last_seen_at >= timedelta(seconds=settings.session_touch_seconds):
        row.last_seen_at = now
        await db.commit()
    return row


async def revoke_session(db: AsyncSession, session_id: int) -> None:
    await db.execute(
        update(AuthSession).where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None)).values(revoked_at=_now())
    )
    await db.commit()


async def revoke_all_sessions(db: AsyncSession, user_id: int) -> int:
    result = await db.execute(
        update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)).values(revoked_at=_now())
    )
    await db.commit()
    return result.rowcount or 0


async def purge_dead_sessions(db: AsyncSession, keep_days: int = 30) -> None:
    """Delete sessions that expired or were revoked more than `keep_days` ago (housekeeping at sign-in)."""
    cutoff = _now() - timedelta(days=keep_days)
    await db.execute(delete(AuthSession).where((AuthSession.expires_at < cutoff) | (AuthSession.revoked_at < cutoff)))
