"""Removing a repository from a workspace: memberships, shared data, running jobs and permissions."""

from unittest.mock import MagicMock

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.auth import settings_dep
from apps.api.core.queue import get_queue
from apps.api.main import app
from apps.api.models.entities import (
    ArchitectureAnalysis,
    CodeChunk,
    Conversation,
    Message,
    Repository,
    SourceFile,
    UserRepository,
)
from tests.auth_support import make_settings
from tests.search_support import ingest, new_repository

pytestmark = pytest.mark.anyio

SHOWCASE = "octo/showcase"


@pytest.fixture(autouse=True)
def isolated(no_auto_membership, app_overrides):
    app.dependency_overrides[settings_dep] = lambda: make_settings(guest_showcase_repositories=SHOWCASE)
    app.dependency_overrides[get_queue] = lambda: MagicMock()
    yield
    for dep in (settings_dep, get_queue):
        app.dependency_overrides.pop(dep, None)


async def count(db: AsyncSession, model, *where) -> int:
    return (await db.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


async def join(db: AsyncSession, user, repo_id: int) -> None:
    db.add(UserRepository(user_id=user.id, repository_id=repo_id))
    await db.commit()


async def converse(db: AsyncSession, user, repo_id: int) -> int:
    conversation = Conversation(user_id=user.id, repository_id=repo_id, title="q")
    db.add(conversation)
    await db.flush()
    db.add(Message(conversation_id=conversation.id, role="user", content="How does it work?"))
    await db.commit()
    return conversation.id


async def test_the_last_member_removes_the_repository_and_everything_stored_for_it(make_client, real_db_session, github_service) -> None:
    alice, a_user = await make_client("alice")
    repo_id = await new_repository(real_db_session, "octo/demo")
    await ingest(real_db_session, repo_id)
    await join(real_db_session, a_user, repo_id)
    await converse(real_db_session, a_user, repo_id)
    real_db_session.add(ArchitectureAnalysis(repository_id=repo_id, status="completed"))
    await real_db_session.commit()
    assert await count(real_db_session, CodeChunk) > 0

    response = await alice.delete(f"/repositories/{repo_id}")

    assert response.status_code == 200
    assert response.json() == {"repository_id": repo_id, "removed": True, "data_deleted": True}
    assert await real_db_session.get(Repository, repo_id) is None
    for model in (SourceFile, CodeChunk, ArchitectureAnalysis, Conversation, Message, UserRepository):
        assert await count(real_db_session, model) == 0, model.__name__
    assert (await alice.get("/repositories")).json() == []
    assert (await alice.get(f"/repositories/{repo_id}")).status_code == 404
    assert github_service.mock_calls == []  # the GitHub repository is never contacted, let alone changed


async def test_removal_by_one_member_keeps_the_shared_repository_for_the_others(make_client, real_db_session) -> None:
    alice, a_user = await make_client("alice")
    bob, b_user = await make_client("bob")
    repo_id = await new_repository(real_db_session, "octo/demo")
    await ingest(real_db_session, repo_id)
    await join(real_db_session, a_user, repo_id)
    await join(real_db_session, b_user, repo_id)
    a_conv = await converse(real_db_session, a_user, repo_id)
    await converse(real_db_session, b_user, repo_id)
    files = await count(real_db_session, SourceFile)

    response = await bob.delete(f"/repositories/{repo_id}")

    assert response.json()["data_deleted"] is False
    assert await count(real_db_session, SourceFile) == files
    assert [r["id"] for r in (await alice.get("/repositories")).json()] == [repo_id]
    assert (await bob.get("/repositories")).json() == []
    assert await count(real_db_session, Conversation, Conversation.user_id == b_user.id) == 0
    assert await count(real_db_session, Conversation, Conversation.id == a_conv) == 1
    assert (await bob.get(f"/repositories/{repo_id}/files")).status_code == 404  # no longer his


async def test_a_showcase_repository_keeps_its_data_when_its_last_member_leaves(make_client, anon_client, real_db_session) -> None:
    alice, a_user = await make_client("alice")
    repo_id = await new_repository(real_db_session, SHOWCASE)
    await ingest(real_db_session, repo_id)
    await join(real_db_session, a_user, repo_id)

    assert (await alice.delete(f"/repositories/{repo_id}")).json()["data_deleted"] is False

    assert (await anon_client.get(f"/repositories/{repo_id}/files")).status_code == 200
    assert (await alice.get(f"/repositories/{repo_id}")).json()["viewer_access"] == "guest"


@pytest.mark.parametrize("status", ["queued", "in_progress"])
async def test_removal_waits_for_a_running_ingestion(make_client, real_db_session, status: str) -> None:
    alice, a_user = await make_client("alice")
    repo_id = await new_repository(real_db_session, "octo/demo")
    await ingest(real_db_session, repo_id)
    await join(real_db_session, a_user, repo_id)
    await real_db_session.execute(update(Repository).where(Repository.id == repo_id).values(ingestion_status=status))
    await real_db_session.commit()

    response = await alice.delete(f"/repositories/{repo_id}")

    assert response.status_code == 409 and "ingested" in response.json()["detail"]
    assert await count(real_db_session, UserRepository) == 1
    assert await real_db_session.get(Repository, repo_id) is not None


async def test_stored_data_is_not_deleted_under_a_running_analysis_but_leaving_is_still_allowed_when_shared(make_client, real_db_session) -> None:
    alice, a_user = await make_client("alice")
    bob, b_user = await make_client("bob")
    repo_id = await new_repository(real_db_session, "octo/demo")
    await ingest(real_db_session, repo_id)
    await join(real_db_session, a_user, repo_id)
    await join(real_db_session, b_user, repo_id)
    real_db_session.add(ArchitectureAnalysis(repository_id=repo_id, status="running"))
    await real_db_session.commit()

    assert (await bob.delete(f"/repositories/{repo_id}")).status_code == 200  # Alice keeps the data, so nothing is deleted

    blocked = await alice.delete(f"/repositories/{repo_id}")  # now it would delete the analysis that is being written
    assert blocked.status_code == 409 and "architecture analysis" in blocked.json()["detail"]
    assert await real_db_session.get(Repository, repo_id) is not None
    assert await count(real_db_session, UserRepository) == 1


async def test_only_members_may_remove_a_repository(make_client, anon_client, real_db_session) -> None:
    alice, a_user = await make_client("alice")
    bob, _b = await make_client("bob")
    private = await new_repository(real_db_session, "octo/demo")
    showcased = await new_repository(real_db_session, SHOWCASE)
    for repo_id in (private, showcased):
        await ingest(real_db_session, repo_id)
    await join(real_db_session, a_user, private)

    assert (await bob.delete(f"/repositories/{private}")).status_code == 404  # ids of other people's repositories stay hidden
    assert (await bob.delete(f"/repositories/{showcased}")).status_code == 403  # browsable, but not Bob's to remove
    assert (await bob.delete("/repositories/999999")).status_code == 404
    for repo_id in (private, showcased):
        assert (await anon_client.delete(f"/repositories/{repo_id}")).status_code in (401, 403)
    assert await real_db_session.get(Repository, private) is not None and await real_db_session.get(Repository, showcased) is not None
    assert await count(real_db_session, UserRepository) == 1


async def test_removal_needs_the_csrf_token(make_client, real_db_session) -> None:
    alice, a_user = await make_client("alice")
    repo_id = await new_repository(real_db_session, "octo/demo")
    await join(real_db_session, a_user, repo_id)
    del alice.headers["X-CSRF-Token"]

    assert (await alice.delete(f"/repositories/{repo_id}")).status_code == 403
    assert await real_db_session.get(Repository, repo_id) is not None


# --- a signup that loses a race with the removal of the last member ------------------------------------------------

def _delete_repository_first(monkeypatch, db: AsyncSession) -> None:
    """Make the repository vanish between the route's lookup and its membership insert, as a concurrent removal would."""
    from sqlalchemy import delete

    from apps.api.routes import repositories as routes

    real_add_member = routes.add_member

    async def add_member_after_removal(session, user_id, repository_id):
        await db.execute(delete(Repository).where(Repository.id == repository_id))
        await real_add_member(session, user_id, repository_id)

    monkeypatch.setattr(routes, "add_member", add_member_after_removal)


async def test_joining_a_stored_repository_that_was_just_removed_asks_to_retry(make_client, real_db_session, monkeypatch) -> None:
    bob, b_user = await make_client("bob")
    bob_id = b_user.id
    repo_id = await new_repository(real_db_session, "octo/demo")
    _delete_repository_first(monkeypatch, real_db_session)

    response = await bob.post("/repositories", json={"github_url": "https://github.com/octo/demo"})

    assert response.status_code == 409 and "Please try again" in response.json()["detail"]
    assert await count(real_db_session, UserRepository, UserRepository.user_id == bob_id) == 0
    assert repo_id  # the 409 came from the lost race, not from a server error


async def test_importing_a_renamed_repository_that_was_just_removed_asks_to_retry(make_client, real_db_session, github_service, monkeypatch) -> None:
    from apps.api.services.github import GitHubRepositoryMetadata

    bob, _b = await make_client("bob")
    repo_id = await new_repository(real_db_session, "octo/old-name")
    stored = await real_db_session.get(Repository, repo_id)
    github_service.get_repository_metadata.return_value = GitHubRepositoryMetadata(stored.github_repo_id, "octo/new-name", "main", None)
    _delete_repository_first(monkeypatch, real_db_session)

    response = await bob.post("/repositories", json={"github_url": "https://github.com/octo/new-name"})

    assert response.status_code == 409 and "Please try again" in response.json()["detail"]


async def test_the_retry_after_a_lost_race_imports_the_repository_again(make_client, real_db_session, github_service) -> None:
    from apps.api.services.github import GitHubRepositoryMetadata

    alice, a_user = await make_client("alice")
    bob, _b = await make_client("bob")
    repo_id = await new_repository(real_db_session, "octo/demo")
    stored = await real_db_session.get(Repository, repo_id)
    github_id = stored.github_repo_id
    await join(real_db_session, a_user, repo_id)
    assert (await alice.delete(f"/repositories/{repo_id}")).json()["data_deleted"] is True
    github_service.get_repository_metadata.return_value = GitHubRepositoryMetadata(github_id, "octo/demo", "main", None)

    response = await bob.post("/repositories", json={"github_url": "https://github.com/octo/demo"})

    assert response.status_code == 201 and response.json()["viewer_access"] == "member"


async def test_constraint_violations_are_recognised_whether_the_driver_error_is_wrapped_or_not() -> None:
    import asyncpg
    from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
    from sqlalchemy.exc import IntegrityError

    from apps.api.routes.repositories import _is_violation

    driver_error = asyncpg.exceptions.UniqueViolationError("duplicate key")
    bare = IntegrityError("INSERT", {}, driver_error)
    wrapped_orig = AsyncAdapt_asyncpg_dbapi.IntegrityError("duplicate key")
    wrapped_orig.__cause__ = driver_error
    wrapped = IntegrityError("INSERT", {}, wrapped_orig)

    assert _is_violation(bare, asyncpg.exceptions.UniqueViolationError) and _is_violation(wrapped, asyncpg.exceptions.UniqueViolationError)
    assert not _is_violation(wrapped, asyncpg.exceptions.ForeignKeyViolationError)
