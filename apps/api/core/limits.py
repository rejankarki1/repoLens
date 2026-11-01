"""Dependencies for usage limits: the limiter, the caller's address, and 429 responses."""

import ipaddress
import logging

from fastapi import Depends, HTTPException, Request
from redis.exceptions import RedisError

from apps.api.config import Settings
from apps.api.core.auth import Principal, get_principal, settings_dep
from apps.api.services.rate_limit import Limiter, RedisLimiter

logger = logging.getLogger(__name__)
SCOPE_HEADER = "X-RateLimit-Scope"


def get_limiter(request: Request) -> Limiter:
    return RedisLimiter(request.app.state.redis)


def client_ip(request: Request, settings: Settings) -> str:
    """The caller's address. X-Forwarded-For is believed only when the direct peer is a configured reverse proxy,
    and then only the hops that are not themselves trusted proxies, read from the right (the left side is client-supplied)."""
    peer = request.client.host if request.client else "unknown"
    networks = settings.trusted_proxy_networks
    if not networks:
        return peer

    def trusted(address: str) -> bool:
        try:
            return any(ipaddress.ip_address(address) in net for net in networks)
        except ValueError:
            return False

    if not trusted(peer):
        return peer
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    for hop in reversed(hops):
        if not trusted(hop):
            return hop
    return peer


def too_many(message: str, retry_after: int, scope: str) -> HTTPException:
    return HTTPException(status_code=429, detail=message, headers={"Retry-After": str(max(1, retry_after)), SCOPE_HEADER: scope})


def unavailable() -> HTTPException:
    """Limits cannot be enforced (Redis is down), so the protected operation is refused rather than left unmetered."""
    return HTTPException(status_code=503, detail="This action is temporarily unavailable; please try again shortly")


def wait_text(seconds: int) -> str:
    if seconds < 90:
        return f"{seconds} seconds"
    if seconds < 5400:
        return f"{round(seconds / 60)} minutes"
    return f"{round(seconds / 3600)} hours"


async def reserve_or_429(limiter: Limiter, key: str, limit: int, window: int, message: str, scope: str):
    """Count one use of `key`; 429 when the window is full, 503 when Redis cannot be asked."""
    try:
        result = await limiter.reserve(key, limit, window)
    except (RedisError, OSError) as exc:
        logger.warning("Rate limiter unavailable (%s)", type(exc).__name__)
        raise unavailable() from exc
    if not result.allowed:
        raise too_many(f"{message} Try again in about {wait_text(result.retry_after)}.", result.retry_after, scope)
    return result


async def guest_read_limit(
    request: Request,
    principal: Principal = Depends(get_principal),
    settings: Settings = Depends(settings_dep),
    limiter: Limiter = Depends(get_limiter),
) -> None:
    """Per-IP cap for signed-out requests to the showcase. Browsing is not blocked if Redis is down (fail open)."""
    if principal.authenticated:
        return
    try:
        result = await limiter.reserve(f"guest:{client_ip(request, settings)}", settings.guest_reads_per_minute, 60)
    except (RedisError, OSError) as exc:
        logger.warning("Guest rate limiter unavailable (%s); allowing the request", type(exc).__name__)
        return
    if not result.allowed:
        raise too_many("Too many requests from this address.", result.retry_after, "guest")
