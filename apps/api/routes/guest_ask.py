"""Live Ask AI for signed-out visitors: showcase repositories only, one question at a time, nothing saved.

Reuses the members' retrieval, prompt, citation validation and event stream. Cost control is server-side: a per-visitor
(cookie), per-address (only if trustworthy) and all-guests daily cap, then the shared AI budget and concurrency slots.
Disabled unless GUEST_AI_ENABLED. Writes, imports, indexing and conversations stay member-only.
"""

import asyncio
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import Settings
from apps.api.core.auth import settings_dep
from apps.api.core.database import get_db
from apps.api.core.limits import (
    get_limiter, require_allowed_origin, set_visitor_cookie, trustworthy_client_ip, unavailable, visitor_id,
)
from apps.api.models.entities import Conversation, Repository
from apps.api.routes.conversations import _prepare, _question, _require_generator, get_sessions
from apps.api.schemas.conversation import MessageCreate
from apps.api.schemas.guest_ask import GuestAskStatus
from apps.api.services import rag
from apps.api.services.access import in_showcase
from apps.api.services.embeddings import GeminiEmbedder, get_embedder
from apps.api.services.generation import Generator, get_generator
from apps.api.services.quota import GuestAiReservation, guest_status_keys, reserve_guest_ai_question
from apps.api.services.rate_limit import Limiter

logger = logging.getLogger(__name__)
router = APIRouter(tags=["guest-ask"])


async def _showcase_repository(db: AsyncSession, repository_id: int, settings: Settings) -> Repository:
    """The repository, only if guest questions are on and it is on the allow-list. Anything else is a plain 404, whoever asks
    (including members of other repositories), so this endpoint can never be used on a private repository."""
    repository = await db.get(Repository, repository_id) if settings.guest_ai_enabled else None
    if repository is None or not in_showcase(repository, settings):
        raise HTTPException(status_code=404, detail="Free questions are not available for this repository")
    return repository


async def _guarded(stream: AsyncIterator[str], reservation: GuestAiReservation) -> AsyncIterator[str]:
    """Pass the stream through and always free the generation slot. The counters are never given back from here: by now
    retrieval has run and the model may be answering, so even an abandoned stream has been paid for."""
    try:
        async for chunk in stream:
            yield chunk
    finally:
        await asyncio.shield(reservation.finish())


@router.get("/repositories/{repository_id}/guest-ask", response_model=GuestAskStatus)
async def guest_ask_status(
    repository_id: int,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    limiter: Limiter = Depends(get_limiter),
    generator: Generator | None = Depends(get_generator),
) -> GuestAskStatus:
    """What the composer shows. Counts nothing and sets no cookie. `enabled` is False (never an error) when guests cannot ask."""
    response.headers["Cache-Control"] = "no-store"
    off = GuestAskStatus(enabled=False, limit=settings.guest_ai_per_visitor_daily, remaining=0, resets_in_seconds=None)
    if not settings.guest_ai_enabled or generator is None:
        return off
    repository = await db.get(Repository, repository_id)
    if repository is None or not in_showcase(repository, settings):
        return off
    visitor, _is_new = visitor_id(request, settings)  # a missing or malformed cookie reads as a visitor with nothing used
    keys = guest_status_keys(visitor, trustworthy_client_ip(request, settings))
    limits = {"visitor": settings.guest_ai_per_visitor_daily, "ip": settings.guest_ai_per_ip_daily, "global": settings.guest_ai_global_daily}
    try:
        usage = {name: await limiter.usage(key) for name, key in keys.items()}
    except (RedisError, OSError) as exc:
        raise unavailable() from exc
    left = {name: max(0, limits[name] - used) for name, (used, _ttl) in usage.items()}
    binding = min(left, key=lambda name: left[name])
    ttl = usage[binding][1]
    return GuestAskStatus(
        enabled=True, limit=settings.guest_ai_per_visitor_daily, remaining=left[binding], resets_in_seconds=ttl or None
    )


@router.post("/repositories/{repository_id}/guest-ask/stream")
async def guest_ask_stream(
    repository_id: int,
    body: MessageCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    generator: Generator | None = Depends(get_generator),
    embedder: GeminiEmbedder | None = Depends(get_embedder),
    sessions=Depends(get_sessions),
    settings: Settings = Depends(settings_dep),
    limiter: Limiter = Depends(get_limiter),
) -> StreamingResponse:
    """Server-sent events like the members' stream (`retrieval`, `token`s, `done` or `error`), but stateless."""
    require_allowed_origin(request, settings)
    repository = await _showcase_repository(db, repository_id, settings)
    question = _question(body)
    generator = _require_generator(generator)
    visitor, is_new = visitor_id(request, settings)
    reservation = await reserve_guest_ai_question(limiter, settings, visitor, trustworthy_client_ip(request, settings))
    try:
        # A transient conversation: no history and nothing is ever added to the session.
        prepared = await _prepare(db, Conversation(id=0, repository_id=repository.id), question, embedder)
    except HTTPException as exc:
        if exc.status_code in (404, 409):  # the repository or its index is missing: refused before any embedding call
            await asyncio.shield(reservation.refund_unused())
        else:
            await asyncio.shield(reservation.finish())
        raise
    except BaseException:
        await asyncio.shield(reservation.finish())  # the query embedding may have been billed: keep the counters
        raise
    streaming = StreamingResponse(
        _guarded(rag.stream_events(prepared, generator=generator, sessions=sessions, persist=False), reservation),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
    set_visitor_cookie(streaming, settings, visitor)  # (re)issued on every answered question; a new visitor gets one here
    return streaming
