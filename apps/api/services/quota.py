"""Usage policies built on the limiter: AI questions, imports, and per-user caps.

AI question policy (documented in docs/auth-design.md):
  1. burst window     counts every attempt and is never refunded (retry floods stay bounded);
  2. daily per user   reserved before any retrieval or provider call, REFUNDED unless an answer was saved;
  3. global daily     the provider budget guard, reserved and refunded together with (2);
  4. global concurrency  a generation slot, always released when the request ends (and freed by TTL if the process dies).
A refused question therefore costs nothing but its burst attempt; a failed one costs nothing at all.
"""

import asyncio
import logging
from dataclasses import dataclass

from fastapi import HTTPException
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import Settings
from apps.api.core.limits import reserve_or_429, too_many, unavailable, wait_text
from apps.api.models.entities import Repository, User, UserRepository
from apps.api.services.rate_limit import Limiter

logger = logging.getLogger(__name__)
DAY = 24 * 3600
GLOBAL_DAY_KEY, GLOBAL_SLOT_KEY = "ai:global:day", "ai:global:slots"


@dataclass
class AiReservation:
    limiter: Limiter
    daily_key: str
    slot_token: str
    finished: bool = False

    async def finish(self, answer_saved: bool) -> None:
        """Call exactly once when the request ends. Keeps the quota if an answer was saved, otherwise gives it back."""
        if self.finished:
            return
        self.finished = True
        try:
            if not answer_saved:
                await self.limiter.refund(self.daily_key)
                await self.limiter.refund(GLOBAL_DAY_KEY)
            await self.limiter.release(GLOBAL_SLOT_KEY, self.slot_token)
        except (RedisError, OSError):
            logger.warning("Could not return an AI quota reservation; it expires on its own")


async def reserve_ai_question(limiter: Limiter, settings: Settings, user_id: int) -> AiReservation:
    s = settings
    await reserve_or_429(
        limiter, f"ai:burst:{user_id}", s.ai_burst_limit, s.ai_burst_window_seconds,
        "You are asking questions too quickly.", "burst",
    )
    daily_key = f"ai:day:{user_id}"
    await reserve_or_429(
        limiter, daily_key, s.ai_daily_limit_per_user, DAY,
        f"You have used your {s.ai_daily_limit_per_user} questions for today.", "daily",
    )
    try:
        await reserve_or_429(
            limiter, GLOBAL_DAY_KEY, s.ai_global_daily_limit, DAY,
            "RepoLens has reached its AI budget for today.", "global",
        )
        try:
            token = await limiter.acquire(GLOBAL_SLOT_KEY, s.ai_global_concurrency, s.ai_lease_seconds)
        except (RedisError, OSError) as exc:
            await limiter.refund(GLOBAL_DAY_KEY)
            raise unavailable() from exc
        if token is None:
            await limiter.refund(GLOBAL_DAY_KEY)
            raise too_many("RepoLens is answering many questions right now. Please try again in a few seconds.", 5, "busy")
    except HTTPException:
        await _quiet_refund(limiter, daily_key)
        raise
    return AiReservation(limiter, daily_key, token)


async def _quiet_refund(limiter: Limiter, key: str) -> None:
    try:
        await limiter.refund(key)
    except (RedisError, OSError):
        logger.warning("Could not refund %s", key)


def shielded(awaitable) -> "asyncio.Future":
    """Run bookkeeping to completion even if the request is being cancelled (a client that went away)."""
    return asyncio.shield(awaitable)


GUEST_GLOBAL_KEY = "guestai:global:day"


@dataclass
class GuestAiReservation:
    """A guest's question. Counters are given back only by `refund_unused` (nothing paid was called yet); once the
    retrieval or the model may have been billed, `finish` keeps them, even if the client abandons the stream."""

    limiter: Limiter
    counted: list[str]  # the counter keys reserved, in order
    slot_token: str
    done: bool = False

    async def _release(self) -> None:
        await self.limiter.release(GLOBAL_SLOT_KEY, self.slot_token)

    async def finish(self) -> None:
        if self.done:
            return
        self.done = True
        try:
            await self._release()
        except (RedisError, OSError):
            logger.warning("Could not release a guest generation slot; it expires on its own")

    async def refund_unused(self) -> None:
        if self.done:
            return
        self.done = True
        try:
            for key in self.counted:
                await self.limiter.refund(key)
            await self._release()
        except (RedisError, OSError):
            logger.warning("Could not return a guest quota reservation; it expires on its own")


def guest_status_keys(visitor: str, ip: str | None) -> dict[str, str]:
    keys = {"visitor": f"guestai:visitor:{visitor}", "global": GUEST_GLOBAL_KEY}
    if ip is not None:
        keys["ip"] = f"guestai:ip:{ip}"
    return keys


async def reserve_guest_ai_question(limiter: Limiter, settings: Settings, visitor: str, ip: str | None) -> GuestAiReservation:
    """429 (or 503 if Redis cannot be asked) before any retrieval or model call. Layers: this visitor, this address (only
    if trustworthy), all guests, then the same shared daily budget and concurrency slots members use."""
    s = settings
    keys = guest_status_keys(visitor, ip)
    layers = [
        ("visitor", keys["visitor"], s.guest_ai_per_visitor_daily, f"You have used your {s.guest_ai_per_visitor_daily} free questions for today.", "guest_visitor"),
    ]
    if "ip" in keys:
        layers.append(("ip", keys["ip"], s.guest_ai_per_ip_daily, "This network has used its free questions for today.", "guest_ip"))
    layers += [
        ("global", GUEST_GLOBAL_KEY, s.guest_ai_global_daily, "The free demo questions for today are used up.", "guest_global"),
        ("shared", GLOBAL_DAY_KEY, s.ai_global_daily_limit, "RepoLens has reached its AI budget for today.", "global"),
    ]
    counted: list[str] = []
    try:
        for _name, key, limit, message, scope in layers:
            await reserve_or_429(limiter, key, limit, DAY, message, scope)
            counted.append(key)
        try:
            token = await limiter.acquire(GLOBAL_SLOT_KEY, s.ai_global_concurrency, s.ai_lease_seconds)
        except (RedisError, OSError) as exc:
            raise unavailable() from exc
        if token is None:
            raise too_many("RepoLens is answering many questions right now. Please try again in a few seconds.", 5, "busy")
    except HTTPException:
        for key in counted:
            await _quiet_refund(limiter, key)
        raise
    return GuestAiReservation(limiter, counted, token)


IMPORT_GLOBAL_KEY = "import:global:day"


async def reserve_import(limiter: Limiter, settings: Settings, user_id: int):
    result = await reserve_or_429(
        limiter, f"import:{user_id}", settings.import_hourly_limit, 3600,
        f"You have started {settings.import_hourly_limit} imports in the last hour.", "import",
    )
    if settings.import_global_daily_limit > 0:  # all users together: bounds GitHub downloads and database growth
        try:
            await reserve_or_429(
                limiter, IMPORT_GLOBAL_KEY, settings.import_global_daily_limit, DAY,
                "RepoLens has reached its import limit for today.", "import_global",
            )
        except HTTPException:
            await _quiet_refund(limiter, f"import:{user_id}")
            raise
    return result


async def refund_import(limiter: Limiter, user_id: int, settings: Settings | None = None) -> None:
    await _quiet_refund(limiter, f"import:{user_id}")
    if settings is not None and settings.import_global_daily_limit > 0:
        await _quiet_refund(limiter, IMPORT_GLOBAL_KEY)


async def reserve_hourly(limiter: Limiter, name: str, user_id: int, limit: int, message: str):
    return await reserve_or_429(limiter, f"{name}:{user_id}", limit, 3600, message, name)


async def refund_hourly(limiter: Limiter, name: str, user_id: int) -> None:
    await _quiet_refund(limiter, f"{name}:{user_id}")


async def check_import_concurrency(db: AsyncSession, settings: Settings, user_id: int, repository_id: int) -> None:
    """At most N of the user's repositories may be queued or running at once. The user's row is locked while counting,
    so two simultaneous requests cannot both see room for one more; the lock ends when the caller commits."""
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())
    active = (
        await db.execute(
            select(func.count())
            .select_from(Repository)
            .join(UserRepository, UserRepository.repository_id == Repository.id)
            .where(
                UserRepository.user_id == user_id,
                Repository.ingestion_status.in_(("queued", "in_progress")),
                Repository.id != repository_id,
            )
        )
    ).scalar_one()
    if active >= settings.import_max_concurrent_per_user:
        raise too_many(
            f"You already have {active} repositories being imported. Wait for one to finish.", 15, "import_concurrency"
        )


async def check_repository_cap(db: AsyncSession, settings: Settings, user_id: int) -> None:
    count = (await db.execute(select(func.count()).select_from(UserRepository).where(UserRepository.user_id == user_id))).scalar_one()
    if count >= settings.max_repositories_per_user:
        raise HTTPException(status_code=403, detail=f"You have reached the limit of {settings.max_repositories_per_user} repositories")


async def check_total_repository_cap(db: AsyncSession, settings: Settings) -> None:
    """Hard ceiling on stored repositories across all users (0 disables it): keeps a small free database from filling up."""
    if settings.max_total_repositories <= 0:
        return
    total = (await db.execute(select(func.count()).select_from(Repository))).scalar_one()
    if total >= settings.max_total_repositories:
        raise HTTPException(
            status_code=403,
            detail="This RepoLens demo has reached its repository capacity. Browse the existing repositories instead.",
        )


__all__ = [
    "AiReservation", "reserve_ai_question", "GuestAiReservation", "reserve_guest_ai_question", "guest_status_keys", "GUEST_GLOBAL_KEY", "reserve_import", "refund_import", "reserve_hourly", "refund_hourly",
    "check_import_concurrency", "check_repository_cap", "check_total_repository_cap", "shielded", "wait_text",
]
