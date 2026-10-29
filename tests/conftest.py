"""Shared pytest fixtures for API tests."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from apps.api.config import get_settings
from apps.api.core.database import get_db
from apps.api.main import app
from apps.api.models.base import Base
from apps.api.services.github import get_github_service

_TEST_DB_NAME = "repolens_test"


@pytest.fixture
def anyio_backend() -> str:
    """Run `@pytest.mark.anyio` tests on asyncio only (no trio dependency)."""
    return "asyncio"


@pytest.fixture
def db_session() -> AsyncMock:
    """A stand-in for a request-scoped AsyncSession, with no real database involved.

    `execute()` resolves to a plain `MagicMock` (not `AsyncMock`) so that result
    accessors like `.scalar_one_or_none()` are regular sync calls, matching the
    real `Result` object returned by SQLAlchemy.
    """
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock())
    session.add = MagicMock()
    return session


@pytest.fixture
def github_service() -> AsyncMock:
    """A stand-in for `GitHubService`, with no real GitHub API calls involved."""
    return AsyncMock()


@pytest.fixture
def client(db_session: AsyncMock, github_service: AsyncMock) -> Iterator[TestClient]:
    """A TestClient whose requests use `db_session` and `github_service` instead of real ones."""

    async def override_get_db() -> AsyncIterator[AsyncMock]:
        yield db_session

    async def override_get_github_service() -> AsyncIterator[AsyncMock]:
        yield github_service

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_github_service] = override_get_github_service
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_github_service, None)


def _test_database_url() -> URL:
    return make_url(get_settings().database_url).set(database=_TEST_DB_NAME)


async def _admin_connection() -> asyncpg.Connection:
    admin_url = make_url(get_settings().database_url)
    return await asyncpg.connect(
        user=admin_url.username,
        password=admin_url.password,
        host=admin_url.host,
        port=admin_url.port,
        database=admin_url.database,
    )


async def _create_test_database() -> None:
    conn = await _admin_connection()
    try:
        try:
            await conn.execute(f"CREATE DATABASE {_TEST_DB_NAME}")
        except asyncpg.exceptions.DuplicateDatabaseError:
            pass
    finally:
        await conn.close()


async def _reset_test_schema() -> None:
    engine = create_async_engine(_test_database_url())
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))  # code_embeddings uses pgvector
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


async def _drop_test_database() -> None:
    conn = await _admin_connection()
    try:
        await conn.execute(f"DROP DATABASE IF EXISTS {_TEST_DB_NAME} WITH (FORCE)")
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def _prepared_test_database() -> Iterator[None]:
    """Create a disposable `repolens_test` database with a fresh schema for this test session.

    Uses a one-off `asyncio.run()` rather than the per-test anyio event loop, since this setup/
    teardown runs once for the whole session while test functions each get their own loop; an
    asyncpg connection can't be reused across event loops. Never touches the real `repolens` dev
    database - only ever creates/drops the literal `repolens_test` database by name.
    """
    asyncio.run(_create_test_database())
    asyncio.run(_reset_test_schema())
    yield
    asyncio.run(_drop_test_database())


@pytest.fixture
async def real_db_session(_prepared_test_database: None) -> AsyncIterator[AsyncSession]:
    """A real `AsyncSession` against `repolens_test`, wrapped in a transaction that is always
    rolled back at teardown - rows are genuinely persisted and queryable during the test, but
    nothing survives it. Standard SQLAlchemy "join a Session into an external transaction" recipe:
    a SAVEPOINT is restarted after every commit the application code makes, so `db.commit()`
    inside the route only ends the savepoint, never the outer transaction.
    """
    engine = create_async_engine(_test_database_url())
    async with engine.connect() as conn:
        await conn.begin()
        await conn.begin_nested()
        session = AsyncSession(bind=conn, expire_on_commit=False)

        @event.listens_for(session.sync_session, "after_transaction_end")
        def _restart_savepoint(sess: object, transaction: object) -> None:
            if conn.closed:
                return
            if not conn.in_nested_transaction():
                conn.sync_connection.begin_nested()

        try:
            yield session
        finally:
            await session.close()
            await conn.rollback()
    await engine.dispose()


@pytest.fixture
async def real_client(
    real_db_session: AsyncSession, github_service: AsyncMock
) -> AsyncIterator[httpx.AsyncClient]:
    """An async client backed by the real, transactionally-isolated `repolens_test` database.

    Uses `httpx.AsyncClient` + `ASGITransport` (not Starlette's sync `TestClient`) so the ASGI app
    runs in the *same* event loop as this test and `real_db_session` - the sync `TestClient` runs
    the app in a separate loop internally, which would hand `real_db_session`'s asyncpg connection
    to a loop other than the one it was created on.
    """

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield real_db_session

    async def override_get_github_service() -> AsyncIterator[AsyncMock]:
        yield github_service

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_github_service] = override_get_github_service
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_github_service, None)
