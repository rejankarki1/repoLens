"""Shared pytest fixtures for API tests."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from apps.api.config import get_settings
from apps.api.core.database import get_db
from apps.api.main import app
from apps.api.models.base import Base
from apps.api.models.entities import GitHubAccount, Repository, User, UserRepository
from apps.api.services.sessions import create_session
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


# Tests create repositories directly in the database. While a signed-in test user is active, each new repository
# is added to that user's workspace, as importing it through the API would do. Isolation tests turn this off.
_AUTO_MEMBER: dict[str, int | None] = {"user_id": None}


@event.listens_for(Repository, "after_insert")
def _auto_membership(mapper, connection, target) -> None:  # noqa: ARG001
    user_id = _AUTO_MEMBER["user_id"]
    if user_id is not None:
        connection.execute(pg_insert(UserRepository).values(user_id=user_id, repository_id=target.id).on_conflict_do_nothing())


@pytest.fixture
def no_auto_membership() -> Iterator[None]:
    """Repositories created while this is active belong to nobody (for isolation tests)."""
    previous = _AUTO_MEMBER["user_id"]
    _AUTO_MEMBER["user_id"] = None
    yield
    _AUTO_MEMBER["user_id"] = previous


@pytest.fixture
async def app_overrides(real_db_session: AsyncSession, github_service: AsyncMock) -> AsyncIterator[None]:
    """Route the app to the transactional test database and a fake GitHub service."""

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield real_db_session

    async def override_get_github_service() -> AsyncIterator[AsyncMock]:
        yield github_service

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_github_service] = override_get_github_service
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_github_service, None)


def _new_http_client() -> httpx.AsyncClient:
    # Same-origin as the web app in the tests, so browsers-style Origin checks see an allowed origin.
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


_user_numbers = iter(range(9_000_000, 10_000_000))


async def make_user(db: AsyncSession, login: str | None = None) -> tuple[User, str, str]:
    """A GitHub-backed user with a live session. Returns (user, raw session cookie, csrf token)."""
    number = next(_user_numbers)
    user = User(email=None)
    db.add(user)
    await db.flush()
    db.add(GitHubAccount(user_id=user.id, github_user_id=number, login=login or f"user{number}", display_name=f"User {number}"))
    token, session = await create_session(db, user.id, get_settings())
    await db.commit()
    return user, token, session.csrf_token


@pytest.fixture
async def anon_client(app_overrides: None) -> AsyncIterator[httpx.AsyncClient]:
    """A visitor: no session cookie."""
    async with _new_http_client() as client:
        yield client


@pytest.fixture
async def make_client(real_db_session: AsyncSession, app_overrides: None):
    """Factory for signed-in clients of distinct users: `client, user = await make_client("alice")`."""
    clients: list[httpx.AsyncClient] = []

    async def factory(login: str | None = None) -> tuple[httpx.AsyncClient, User]:
        user, token, csrf = await make_user(real_db_session, login)
        client = _new_http_client()
        client.cookies.set(get_settings().session_cookie_name, token)
        client.headers["X-CSRF-Token"] = csrf
        clients.append(client)
        return client, user

    yield factory
    for client in clients:
        await client.aclose()


@pytest.fixture
async def test_user(real_db_session: AsyncSession) -> AsyncIterator[tuple[User, str, str]]:
    user, token, csrf = await make_user(real_db_session, "tester")
    _AUTO_MEMBER["user_id"] = user.id
    try:
        yield user, token, csrf
    finally:
        _AUTO_MEMBER["user_id"] = None


@pytest.fixture
async def real_client(app_overrides: None, test_user: tuple[User, str, str]) -> AsyncIterator[httpx.AsyncClient]:
    """The client most tests use: signed in as `test_user`, who is automatically a member of repositories the test creates."""
    _user, token, csrf = test_user
    async with _new_http_client() as client:
        client.cookies.set(get_settings().session_cookie_name, token)
        client.headers["X-CSRF-Token"] = csrf
        yield client
