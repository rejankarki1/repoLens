"""The authentication migrations on disposable databases: a fresh one, and one holding Phase 1 development data."""

import os
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from apps.api.config import get_settings

pytestmark = pytest.mark.anyio

ROOT = Path(__file__).resolve().parent.parent
PHASE1_HEAD = "b84f2c6d1e07"
SCRATCH = "repolens_auth_migration_test"


def _server() -> dict:
    url = make_url(get_settings().database_url)
    return dict(user=url.username, password=url.password, host=url.host, port=url.port)


def alembic(*args: str) -> subprocess.CompletedProcess:
    url = make_url(get_settings().database_url).set(database=SCRATCH).render_as_string(hide_password=False)
    env = {**os.environ, "DATABASE_URL": url}
    result = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-1500:]
    return result


@pytest.fixture
async def scratch():
    admin = await asyncpg.connect(database="postgres", **_server())
    await admin.execute(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)")
    await admin.execute(f"CREATE DATABASE {SCRATCH}")
    conn = await asyncpg.connect(database=SCRATCH, **_server())
    try:
        yield conn
    finally:
        await conn.close()
        await admin.execute(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)")
        await admin.close()


async def seed_phase1(conn: asyncpg.Connection) -> None:
    """What a development database looks like before authentication: one local user owning everything."""
    local = await conn.fetchval("INSERT INTO users (email) VALUES ('local@repolens.local') RETURNING id")
    other = await conn.fetchval("INSERT INTO users (email) VALUES ('someone-else@example.com') RETURNING id")
    repos = [
        await conn.fetchval(
            "INSERT INTO repositories (github_repo_id, full_name, default_branch, ingestion_status) VALUES ($1, $2, 'main', 'completed') RETURNING id",
            n, f"octo/repo{n}",
        )
        for n in (1, 2, 3)
    ]
    conv = await conn.fetchval("INSERT INTO conversations (user_id, repository_id, title) VALUES ($1, $2, 'kept') RETURNING id", local, repos[0])
    await conn.execute("INSERT INTO messages (conversation_id, role, content) VALUES ($1, 'user', 'hello')", conv)
    await conn.execute(
        "INSERT INTO source_files (repository_id, commit_sha, path, language, category, content, content_hash, size_bytes, line_count) "
        "VALUES ($1, $2, 'a.py', 'python', 'source', 'x = 1', 'h', 5, 1)", repos[0], "a" * 40,
    )
    return local, other, repos, conv


async def test_fresh_database_migrates_up_down_and_up_with_no_drift(scratch: asyncpg.Connection) -> None:
    alembic("upgrade", "head")
    assert (await scratch.fetchval("SELECT count(*) FROM user_repositories")) == 0  # no local user, nothing to backfill
    assert "No new upgrade operations" in alembic("check").stdout + alembic("check").stderr
    alembic("downgrade", PHASE1_HEAD)
    tables = {r["tablename"] for r in await scratch.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    assert "sessions" not in tables and "user_repositories" not in tables
    alembic("upgrade", "head")


async def test_phase1_data_survives_and_the_local_user_keeps_access(scratch: asyncpg.Connection) -> None:
    alembic("upgrade", PHASE1_HEAD)
    local, other, repos, conv = await seed_phase1(scratch)
    alembic("upgrade", "head")

    # nothing was lost or reassigned
    assert await scratch.fetchval("SELECT count(*) FROM repositories") == 3
    assert await scratch.fetchval("SELECT user_id FROM conversations WHERE id=$1", conv) == local
    assert await scratch.fetchval("SELECT count(*) FROM messages WHERE conversation_id=$1", conv) == 1
    assert await scratch.fetchval("SELECT count(*) FROM source_files") == 1
    # only the local development user was made a member, of every existing repository
    members = await scratch.fetch("SELECT user_id, repository_id FROM user_repositories ORDER BY repository_id")
    assert [(m["user_id"], m["repository_id"]) for m in members] == [(local, r) for r in repos]
    assert await scratch.fetchval("SELECT count(*) FROM user_repositories WHERE user_id=$1", other) == 0
    assert "No new upgrade operations" in alembic("check").stdout + alembic("check").stderr


async def test_constraints_indexes_and_deletion_behaviour(scratch: asyncpg.Connection) -> None:
    alembic("upgrade", "head")
    # GitHub users have no email: several NULLs are allowed, a repeated real email is not
    u1 = await scratch.fetchval("INSERT INTO users (email) VALUES (NULL) RETURNING id")
    u2 = await scratch.fetchval("INSERT INTO users (email) VALUES (NULL) RETURNING id")
    await scratch.execute("INSERT INTO users (email) VALUES ('a@example.com')")
    with pytest.raises(asyncpg.UniqueViolationError):
        await scratch.execute("INSERT INTO users (email) VALUES ('a@example.com')")
    await scratch.execute("INSERT INTO github_accounts (user_id, github_user_id, login) VALUES ($1, 77, 'one')", u1)
    with pytest.raises(asyncpg.UniqueViolationError):  # one GitHub identity, one user
        await scratch.execute("INSERT INTO github_accounts (user_id, github_user_id, login) VALUES ($1, 77, 'two')", u2)
    repo = await scratch.fetchval("INSERT INTO repositories (github_repo_id, full_name, default_branch) VALUES (5, 'o/r', 'main') RETURNING id")
    await scratch.execute("INSERT INTO user_repositories (user_id, repository_id) VALUES ($1, $2)", u1, repo)
    with pytest.raises(asyncpg.UniqueViolationError):
        await scratch.execute("INSERT INTO user_repositories (user_id, repository_id) VALUES ($1, $2)", u1, repo)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await scratch.execute("INSERT INTO user_repositories (user_id, repository_id) VALUES ($1, 999999)", u1)
    await scratch.execute(
        "INSERT INTO sessions (user_id, token_hash, csrf_token, expires_at) VALUES ($1, 'h', 'c', now() + interval '1 day')", u1
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await scratch.execute("INSERT INTO sessions (user_id, token_hash, csrf_token, expires_at) VALUES ($1, 'h', 'c', now())", u2)
    conv = await scratch.fetchval("INSERT INTO conversations (user_id, repository_id) VALUES ($1, $2) RETURNING id", u1, repo)

    indexes = {r["indexname"] for r in await scratch.fetch("SELECT indexname FROM pg_indexes WHERE schemaname='public'")}
    assert {"ix_sessions_user_id", "ix_user_repositories_repository_id"} <= indexes

    # deleting a repository removes memberships but keeps the user's conversations (repository_id -> NULL)
    await scratch.execute("DELETE FROM repositories WHERE id=$1", repo)
    assert await scratch.fetchval("SELECT count(*) FROM user_repositories") == 0
    assert await scratch.fetchval("SELECT count(*) FROM conversations WHERE id=$1", conv) == 1
    # deleting a user removes their sessions, conversations and GitHub account
    await scratch.execute("DELETE FROM users WHERE id=$1", u1)
    assert await scratch.fetchval("SELECT count(*) FROM sessions") == 0
    assert await scratch.fetchval("SELECT count(*) FROM conversations") == 0
    assert await scratch.fetchval("SELECT count(*) FROM github_accounts") == 0
