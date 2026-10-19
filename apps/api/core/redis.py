"""Redis client lifecycle and reusable FastAPI dependency."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from apps.api.config import get_settings


logger = logging.getLogger(__name__)


@asynccontextmanager
async def redis_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create one shared client for the app and close it on shutdown."""
    client = Redis.from_url(
        get_settings().redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    app.state.redis = client
    try:
        try:
            await client.ping()
        except (RedisError, OSError) as exc:
            logger.warning("Redis is unavailable at startup: %s", exc)
        yield
    finally:
        await client.aclose()
        del app.state.redis


async def get_redis(request: Request) -> Redis:
    """Give routes access to the app's shared Redis client."""
    return request.app.state.redis
