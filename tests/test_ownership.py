"""Authorization: personal data is isolated per user, shared repository data is gated, guests are read-only."""

from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.auth import settings_dep
from apps.api.core.queue import get_queue
from apps.api.main import app
from apps.api.models.entities import Conversation, Message, UserRepository
from apps.api.services import rag
from apps.api.services.embeddings import get_embedder
from apps.api.services.generation import get_generator
from tests.auth_support import make_settings
from tests.search_support import FakeEmbedder, ingest, new_repository
from tests.test_rag_api import FakeGenerator

pytestmark = pytest.mark.anyio

SHOWCASE = "octo/showcase"


@pytest.fixture(autouse=True)
def isolated(no_auto_membership):
    """Every repository in these tests belongs to nobody until the test says so."""
    app.dependency_overrides[settings_dep] = lambda: make_settings(guest_showcase_repositories=SHOWCASE)
    app.dependency_overrides[get_queue] = lambda: MagicMock()
    app.dependency_overrides[get_generator] = lambda: FakeGenerator()
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    yield
    for dep in (settings_dep, get_queue, get_generator, get_embedder):
        app.dependency_overrides.pop(dep, None)


async def member(db: AsyncSession, user, repo_id: int) -> None:
    db.add(UserRepository(user_id=user.id, repository_id=repo_id))
    await db.commit()


async def two_members(make_client, db: AsyncSession, name: str = "octo/demo"):
    """Alice and Bob both added the same public repository (one shared row)."""
    alice, a_user = await make_client("alice")
    bob, b_user = await make_client("bob")
    repo_id = await new_repository(db, name)
    await ingest(db, repo_id)
    await member(db, a_user, repo_id)
    await member(db, b_user, repo_id)
    return alice, bob, a_user, b_user, repo_id


async def ask(client: httpx.AsyncClient, repo_id: int, conv_id: int, text: str = "How are passwords hashed?") -> httpx.Response:
    return await client.post(f"/repositories/{repo_id}/conversations/{conv_id}/messages", json={"content": text})


# --- personal conversations -------------------------------------------------------------------------------------

async def test_users_cannot_read_rename_delete_or_continue_each_others_conversations(make_client, real_db_session) -> None:
    alice, bob, _a, _b, repo_id = await two_members(make_client, real_db_session)
    conv_id = (await alice.post(f"/repositories/{repo_id}/conversations")).json()["id"]
    assert (await ask(alice, repo_id, conv_id)).status_code == 201
    base = f"/repositories/{repo_id}/conversations/{conv_id}"

    assert (await bob.get(base)).status_code == 404
    assert (await bob.patch(base, json={"title": "stolen"})).status_code == 404
    assert (await ask(bob, repo_id, conv_id)).status_code == 404
    assert (await bob.post(base + "/messages/stream", json={"content": "hello there"})).status_code == 404
    assert (await bob.delete(base)).status_code == 404
    # nothing of Alice's changed
    detail = (await alice.get(base)).json()
    assert detail["title"] != "stolen" and len(detail["messages"]) == 2


async def test_conversation_lists_and_clear_history_are_per_user(make_client, real_db_session) -> None:
    alice, bob, _a, _b, repo_id = await two_members(make_client, real_db_session)
    a_conv = (await alice.post(f"/repositories/{repo_id}/conversations")).json()["id"]
    b_conv = (await bob.post(f"/repositories/{repo_id}/conversations")).json()["id"]
    await ask(alice, repo_id, a_conv)
    await ask(bob, repo_id, b_conv)

    assert [c["id"] for c in (await alice.get(f"/repositories/{repo_id}/conversations")).json()] == [a_conv]
    assert [c["id"] for c in (await bob.get(f"/repositories/{repo_id}/conversations")).json()] == [b_conv]

    cleared = await bob.delete(f"/repositories/{repo_id}/conversations")
    assert cleared.json() == {"deleted": 1}
    assert [c["id"] for c in (await alice.get(f"/repositories/{repo_id}/conversations")).json()] == [a_conv]
    assert (await real_db_session.execute(select(func.count()).select_from(Message))).scalar_one() == 2


@pytest.mark.parametrize("how", ["query", "body", "header", "path"])
async def test_a_user_id_supplied_by_the_browser_is_ignored(make_client, real_db_session, how: str) -> None:
    alice, bob, a_user, _b, repo_id = await two_members(make_client, real_db_session)
    a_conv = (await alice.post(f"/repositories/{repo_id}/conversations")).json()["id"]
    await ask(alice, repo_id, a_conv)
    url = f"/repositories/{repo_id}/conversations/{a_conv}"
    kwargs: dict = {}
    if how == "query":
        kwargs["params"] = {"user_id": a_user.id}
    elif how == "header":
        kwargs["headers"] = {"X-User-Id": str(a_user.id), "X-Forwarded-User": str(a_user.id)}
    elif how == "body":
        response = await bob.post(f"/repositories/{repo_id}/conversations", json={"title": "x", "user_id": a_user.id})
        created = response.json()["id"]
        owner = (await real_db_session.execute(select(Conversation.user_id).where(Conversation.id == created))).scalar_one()
        assert owner != a_user.id  # the new conversation is Bob's, whatever the body said
        return
    elif how == "path":
        url = f"/users/{a_user.id}" + url
        assert (await bob.get(url)).status_code == 404
        return
    assert (await bob.get(url, **kwargs)).status_code == 404


async def test_created_conversation_belongs_to_the_session_user(make_client, real_db_session) -> None:
    alice, _bob, a_user, _b, repo_id = await two_members(make_client, real_db_session)
    conv_id = (await alice.post(f"/repositories/{repo_id}/conversations")).json()["id"]
    owner = (await real_db_session.execute(select(Conversation.user_id).where(Conversation.id == conv_id))).scalar_one()
    assert owner == a_user.id


# --- repository workspaces --------------------------------------------------------------------------------------

async def test_workspace_lists_and_reads_are_limited_to_members(make_client, real_db_session) -> None:
    alice, _a = await make_client("alice")
    bob, b_user = await make_client("bob")
    bobs = await new_repository(real_db_session, "bob/private-interest")
    await ingest(real_db_session, bobs)
    await member(real_db_session, b_user, bobs)

    assert (await alice.get("/repositories")).json() == []
    assert [r["id"] for r in (await bob.get("/repositories")).json()] == [bobs]
    for path in (
        f"/repositories/{bobs}",
        f"/repositories/{bobs}/files",
        f"/repositories/{bobs}/files/content?path=app.py",
        f"/repositories/{bobs}/ingestion-status",
        f"/repositories/{bobs}/architecture/status",
        f"/repositories/{bobs}/architecture",
        f"/repositories/{bobs}/architecture/nodes?id=module:.",
        f"/repositories/{bobs}/search?q=hash",
        f"/repositories/{bobs}/search-index",
        f"/repositories/{bobs}/conversations",
    ):
        response = await alice.get(path)
        assert response.status_code == 404, path  # not even a hint that the repository exists
        assert (await alice.get(path.replace(str(bobs), "99999999"))).status_code == 404  # identical for a missing id


async def test_non_members_cannot_use_write_endpoints(make_client, real_db_session) -> None:
    alice, _a = await make_client("alice")
    _bob, b_user = await make_client("bob")
    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id)
    await member(real_db_session, b_user, repo_id)
    for method, path in (
        ("POST", f"/repositories/{repo_id}/ingestions"),
        ("POST", f"/repositories/{repo_id}/embeddings"),
        ("POST", f"/repositories/{repo_id}/architecture"),
        ("POST", f"/repositories/{repo_id}/conversations"),
        ("DELETE", f"/repositories/{repo_id}/conversations"),
    ):
        assert (await alice.request(method, path)).status_code == 404, (method, path)
    assert (await real_db_session.execute(select(func.count()).select_from(Conversation))).scalar_one() == 0


async def test_importing_a_known_repository_adds_it_to_the_workspace_without_a_copy(
    make_client, real_db_session, github_service
) -> None:
    from apps.api.services.github import GitHubRepositoryMetadata
    from apps.api.models.entities import Repository

    github_service.get_repository_metadata.return_value = GitHubRepositoryMetadata(424242, "octo/shared", "main", None)
    alice, _a = await make_client("alice")
    bob, _b = await make_client("bob")
    first = await alice.post("/repositories", json={"github_url": "https://github.com/octo/shared"})
    assert first.status_code == 201
    assert (await bob.get(f"/repositories/{first.json()['id']}")).status_code == 404  # not Bob's yet
    second = await bob.post("/repositories", json={"github_url": "https://github.com/octo/shared"})
    assert second.status_code == 200 and second.json()["id"] == first.json()["id"]
    assert (await real_db_session.execute(select(func.count()).select_from(Repository).where(Repository.github_repo_id == 424242))).scalar_one() == 1
    assert [r["id"] for r in (await bob.get("/repositories")).json()] == [first.json()["id"]]
    assert (await bob.get(f"/repositories/{first.json()['id']}")).status_code == 200


# --- visitors and the guest showcase ----------------------------------------------------------------------------

async def test_visitors_see_only_the_showcase(anon_client, make_client, real_db_session) -> None:
    other = await new_repository(real_db_session, "octo/not-listed")
    showcased = await new_repository(real_db_session, "Octo/Showcase")  # matching ignores case
    await ingest(real_db_session, showcased)
    listing = (await anon_client.get("/repositories")).json()
    assert [(r["id"], r["viewer_access"]) for r in listing] == [(showcased, "guest")]
    assert (await anon_client.get(f"/repositories/{showcased}")).status_code == 200
    assert (await anon_client.get(f"/repositories/{showcased}/files")).status_code == 200
    assert (await anon_client.get(f"/repositories/{showcased}/ingestion-status")).status_code == 200
    # a repository that is not listed is not public, and a missing id looks the same
    assert (await anon_client.get(f"/repositories/{other}")).status_code == 401
    assert (await anon_client.get("/repositories/99999999")).status_code == 401
    assert (await anon_client.get(f"/repositories/{other}/files")).status_code == 401


async def test_empty_showcase_exposes_nothing(anon_client, real_db_session) -> None:
    app.dependency_overrides[settings_dep] = lambda: make_settings(guest_showcase_repositories="")
    repo_id = await new_repository(real_db_session, "octo/demo")
    assert (await anon_client.get("/repositories")).json() == []
    assert (await anon_client.get(f"/repositories/{repo_id}")).status_code == 401


async def test_guests_cannot_start_imports_jobs_search_or_ai(anon_client, real_db_session) -> None:
    repo_id = await new_repository(real_db_session, SHOWCASE)
    await ingest(real_db_session, repo_id)
    attempts = [
        ("POST", "/repositories", {"json": {"github_url": "https://github.com/octo/anything"}}),
        ("POST", f"/repositories/{repo_id}/ingestions", {}),
        ("POST", f"/repositories/{repo_id}/embeddings", {}),
        ("POST", f"/repositories/{repo_id}/architecture", {}),
        ("GET", f"/repositories/{repo_id}/search?q=hash", {}),
        ("GET", f"/repositories/{repo_id}/search-index", {}),
        ("GET", f"/repositories/{repo_id}/conversations", {}),
        ("POST", f"/repositories/{repo_id}/conversations", {}),
        ("DELETE", f"/repositories/{repo_id}/conversations", {}),
        ("POST", f"/repositories/{repo_id}/conversations/1/messages", {"json": {"content": "hi there"}}),
        ("POST", f"/repositories/{repo_id}/conversations/1/messages/stream", {"json": {"content": "hi there"}}),
    ]
    for method, path, kwargs in attempts:
        response = await anon_client.request(method, path, **kwargs)
        assert response.status_code == 401, (method, path, response.status_code)


async def test_signed_in_users_browse_the_showcase_read_only_until_they_add_it(make_client, real_db_session, github_service) -> None:
    from apps.api.services.github import GitHubRepositoryMetadata

    repo_id = await new_repository(real_db_session, SHOWCASE)
    await ingest(real_db_session, repo_id)
    alice, _a = await make_client("alice")
    assert (await alice.get("/repositories")).json() == []  # the dashboard is only their own
    assert (await alice.get(f"/repositories/{repo_id}")).json()["viewer_access"] == "guest"
    assert (await alice.get(f"/repositories/{repo_id}/files")).status_code == 200
    response = await alice.post(f"/repositories/{repo_id}/conversations")
    assert response.status_code == 403 and "Add this repository" in response.json()["detail"]
    assert (await alice.post(f"/repositories/{repo_id}/ingestions")).status_code == 403
    assert (await alice.post(f"/repositories/{repo_id}/architecture")).status_code == 403

    github_service.get_repository_metadata.return_value = GitHubRepositoryMetadata(
        (await real_db_session.get(__import__("apps.api.models.entities", fromlist=["Repository"]).Repository, repo_id)).github_repo_id, SHOWCASE, "main", None
    )
    assert (await alice.post("/repositories", json={"github_url": f"https://github.com/{SHOWCASE}"})).status_code == 200
    assert (await alice.get(f"/repositories/{repo_id}")).json()["viewer_access"] == "member"
    assert (await alice.post(f"/repositories/{repo_id}/conversations")).status_code == 201


async def test_the_showcase_never_exposes_personal_data(anon_client, make_client, real_db_session) -> None:
    alice, a_user = await make_client("alice")
    repo_id = await new_repository(real_db_session, SHOWCASE)
    await ingest(real_db_session, repo_id)
    await member(real_db_session, a_user, repo_id)
    conv_id = (await alice.post(f"/repositories/{repo_id}/conversations")).json()["id"]
    await ask(alice, repo_id, conv_id)
    base = f"/repositories/{repo_id}/conversations"
    assert (await anon_client.get(base)).status_code == 401
    assert (await anon_client.get(f"{base}/{conv_id}")).status_code == 401
    body = (await anon_client.get(f"/repositories/{repo_id}")).text + (await anon_client.get("/repositories")).text
    assert "alice" not in body.lower() and "user_id" not in body


# --- CSRF and background jobs -----------------------------------------------------------------------------------

WRITES = [
    ("POST", "/repositories", {"github_url": "https://github.com/octo/x"}),
    ("POST", "/repositories/{id}/ingestions", None),
    ("POST", "/repositories/{id}/embeddings", None),
    ("POST", "/repositories/{id}/architecture", None),
    ("POST", "/repositories/{id}/conversations", None),
    ("DELETE", "/repositories/{id}/conversations", None),
    ("PATCH", "/repositories/{id}/conversations/1", {"title": "t"}),
    ("DELETE", "/repositories/{id}/conversations/1", None),
    ("POST", "/repositories/{id}/conversations/1/messages", {"content": "hi there"}),
    ("POST", "/repositories/{id}/conversations/1/messages/stream", {"content": "hi there"}),
]


@pytest.mark.parametrize("method, path, body", WRITES)
async def test_every_state_changing_endpoint_requires_the_csrf_token(make_client, real_db_session, method, path, body) -> None:
    alice, a_user = await make_client("alice")
    repo_id = await new_repository(real_db_session)
    await member(real_db_session, a_user, repo_id)
    path = path.format(id=repo_id)
    before = (await real_db_session.execute(select(func.count()).select_from(Conversation))).scalar_one()

    del alice.headers["X-CSRF-Token"]  # a cross-site form post carries the cookie but not the token
    assert (await alice.request(method, path, json=body)).status_code == 403, (method, path)
    alice.headers["X-CSRF-Token"] = "not-the-token"
    assert (await alice.request(method, path, json=body)).status_code == 403, (method, path)
    alice.headers["X-CSRF-Token"] = "x"
    response = await alice.request(method, path, json=body, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert (await real_db_session.execute(select(func.count()).select_from(Conversation))).scalar_one() == before


async def test_background_job_endpoints_reject_unauthorised_callers(anon_client, make_client, real_db_session) -> None:
    alice, _a = await make_client("alice")
    repo_id = await new_repository(real_db_session)
    for client, expected in ((anon_client, 401), (alice, 404)):
        for method, path in (
            ("POST", f"/repositories/{repo_id}/ingestions"),
            ("GET", f"/repositories/{repo_id}/ingestion-status"),
            ("POST", f"/repositories/{repo_id}/embeddings"),
            ("POST", f"/repositories/{repo_id}/architecture"),
            ("GET", f"/repositories/{repo_id}/architecture/status"),
        ):
            assert (await client.request(method, path)).status_code == expected, (method, path)


async def test_members_can_use_the_job_endpoints(make_client, real_db_session) -> None:
    alice, a_user = await make_client("alice")
    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id)
    await member(real_db_session, a_user, repo_id)
    assert (await alice.get(f"/repositories/{repo_id}/ingestion-status")).status_code == 200
    assert (await alice.post(f"/repositories/{repo_id}/ingestions")).status_code == 202
    assert (await alice.get(f"/repositories/{repo_id}/architecture/status")).status_code == 200


async def test_the_old_single_user_helpers_are_gone() -> None:
    assert not hasattr(rag, "default_user_id") and not hasattr(rag, "find_default_user_id")
