"""FastAPI application and service health endpoints."""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text as sql_text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import get_settings
from apps.api.core.database import engine, get_db, session_factory
from apps.api.core.queue import get_queue
from apps.api.core.redis import get_redis, redis_lifespan
from apps.api.jobs.maintenance import sweeper_loop
from apps.api.routes.architecture import router as architecture_router
from apps.api.routes.conversations import router as conversations_router
from apps.api.routes.files import router as files_router
from apps.api.routes.ingestions import router as ingestions_router
from apps.api.routes.repositories import router as repositories_router
from apps.api.routes.search import router as search_router


logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage Redis startup and release service connections on shutdown."""
    sweeper: asyncio.Task[None] | None = None
    if settings.ingestion_sweep_interval_seconds > 0:
        sweeper = asyncio.create_task(
            sweeper_loop(session_factory, get_queue(), settings.ingestion_sweep_interval_seconds)
        )
    try:
        async with redis_lifespan(app):
            yield
    finally:
        if sweeper is not None:
            sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper
        await engine.dispose()


app = FastAPI(
    title="repoLens API",
    version="0.1.0",
    description="Backend API for repoLens",
    lifespan=lifespan,
)

app.include_router(repositories_router)
app.include_router(ingestions_router)
app.include_router(files_router)
app.include_router(search_router)
app.include_router(conversations_router)
app.include_router(architecture_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)



@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    """Return basic API information."""
    return {"name": "repoLens API", "version": "0.1.0", "docs": "/docs"}


@app.get ("/hello" , tags =["system"])
async def hello () -> dict [str, str]:
    return {"message" : "hello, world!"}


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    """Liveness check: the API process can respond."""
    return {"status": "ok"}


@app.get("/health/ready", tags=["system"])
async def readiness_check(
    db: AsyncSession = Depends(get_db), redis: Redis = Depends(get_redis)
) -> JSONResponse:
    """Readiness check: both PostgreSQL and Redis can respond."""
    checks = {"database": "ok", "redis": "ok"}

    try:
        await db.execute(sql_text("SELECT 1"))
    except (SQLAlchemyError, OSError) as exc:
        logger.warning("PostgreSQL readiness check failed: %s", exc)
        checks["database"] = "unavailable"

    try:
        await redis.ping()
    except (RedisError, OSError) as exc:
        logger.warning("Redis readiness check failed: %s", exc)
        checks["redis"] = "unavailable"

    ready = all(value == "ok" for value in checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "unavailable", "checks": checks},
    )
