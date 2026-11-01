"""Usage limits through HTTP: AI questions, imports, background jobs, guests, sign-in traffic, client IPs."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.auth import settings_dep
from apps.api.core.limits import client_ip, get_limiter
from apps.api.core.queue import get_queue
from apps.api.core.redis import get_redis
from apps.api.main import app
from apps.api.models.entities import Conversation, Repository, UserRepository
from apps.api.services import github_oauth as oauth
from apps.api.services.embeddings import get_embedder
from apps.api.services.generation import GenerationError, get_generator
from apps.api.services.github import GitHubRepositoryMetadata
from tests.auth_support import FakeOAuth, FakeRedis, identity, make_settings
from tests.search_support import FakeEmbedder, ingest, new_repository
from tests.test_rag_api import FakeGenerator

pytestmark = pytest.mark.anyio


@pytest.fixture
def generator() -> FakeGenerator:
    return FakeGenerator()


@pytest.fixture
def limits(app_overrides, generator):
    """`limits(ai_daily_limit_per_user=2, ...)` sets the limits for the test (everything else stays generous)."""
    app.dependency_overrides[get_generator] = lambda: generator
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    app.dependency_overrides[get_queue] = lambda: MagicMock()

    def apply(**overrides) -> None:
        settings = make_settings(**overrides)
        app.dependency_overrides[settings_dep] = lambda: settings

    yield apply
    for dep in (get_generator, get_embedder, get_queue):
        app.dependency_overrides.pop(dep, None)


async def conversation(client: httpx.AsyncClient, repo_id: int) -> int:
    return (await client.post(f"/repositories/{repo_id}/conversations")).json()["id"]


async def ask(client, repo_id, conv_id, text="How are passwords hashed?", stream=False) -> httpx.Response:
    suffix = "/messages/stream" if stream else "/messages"
    return await client.post(f"/repositories/{repo_id}/conversations/{conv_id}{suffix}", json={"content": text})


async def setup_member(real_client, real_db_session, name="octo/demo"):
    repo_id = await new_repository(real_db_session, name)
    await ingest(real_db_session, repo_id)
    return repo_id, await conversation(real_client, repo_id)


# --- AI questions -----------------------------------------------------------------------------------------------

async def test_daily_quota_returns_429_with_retry_after_and_never_reaches_the_provider(limits, real_client, real_db_session, generator) -> None:
    limits(ai_daily_limit_per_user=2)
    repo_id, conv_id = await setup_member(real_client, real_db_session)
    assert [(await ask(real_client, repo_id, conv_id)).status_code for _ in range(2)] == [201, 201]
    calls = len(generator.calls)

    response = await ask(real_client, repo_id, conv_id)
    assert response.status_code == 429
    assert response.headers["X-RateLimit-Scope"] == "daily" and int(response.headers["Retry-After"]) > 3600
    assert "2 questions for today" in response.json()["detail"] and "Try again in about" in response.json()["detail"]
    assert len(generator.calls) == calls  # refused before retrieval or any model call
    assert (await real_db_session.execute(select(func.count()).select_from(Conversation))).scalar_one() == 1


async def test_quotas_are_per_user(limits, make_client, real_db_session) -> None:
    limits(ai_daily_limit_per_user=1)
    alice, a_user = await make_client("alice")
    bob, b_user = await make_client("bob")
    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id)
    for user in (a_user, b_user):
        real_db_session.add(UserRepository(user_id=user.id, repository_id=repo_id))
    await real_db_session.commit()
    a_conv, b_conv = await conversation(alice, repo_id), await conversation(bob, repo_id)
    assert (await ask(alice, repo_id, a_conv)).status_code == 201
    assert (await ask(alice, repo_id, a_conv)).status_code == 429
    assert (await ask(bob, repo_id, b_conv)).status_code == 201  # Alice's quota is not Bob's


async def test_burst_limit_counts_attempts_even_failed_ones(limits, real_client, real_db_session, generator) -> None:
    limits(ai_burst_limit=2, ai_daily_limit_per_user=100)
    repo_id, conv_id = await setup_member(real_client, real_db_session)
    generator.error = GenerationError("unavailable", "provider down")
    assert [(await ask(real_client, repo_id, conv_id)).status_code for _ in range(2)] == [503, 503]
    response = await ask(real_client, repo_id, conv_id)
    assert response.status_code == 429 and response.headers["X-RateLimit-Scope"] == "burst"


async def test_failed_answers_do_not_use_the_daily_quota(limits, real_client, real_db_session, generator) -> None:
    limits(ai_daily_limit_per_user=1, ai_burst_limit=100)
    repo_id, conv_id = await setup_member(real_client, real_db_session)
    generator.error = GenerationError("rate_limited", "provider busy", retry_after=3)
    for _ in range(4):
        assert (await ask(real_client, repo_id, conv_id)).status_code == 429  # the provider's own 429 ...
    generator.error = None
    assert (await ask(real_client, repo_id, conv_id)).status_code == 201  # ... cost nothing: the one daily question is intact
    followup = await ask(real_client, repo_id, conv_id)
    assert followup.status_code == 429 and followup.headers["X-RateLimit-Scope"] == "daily"  # a saved answer does count


async def test_a_question_that_cannot_be_answered_yet_is_not_charged(limits, real_client, real_db_session) -> None:
    limits(ai_daily_limit_per_user=1, ai_burst_limit=100)
    repo_id = await new_repository(real_db_session)  # never ingested: 409 before any model call
    conv_id = await conversation(real_client, repo_id)
    for _ in range(3):
        assert (await ask(real_client, repo_id, conv_id)).status_code == 409
    await ingest(real_db_session, repo_id)
    assert (await ask(real_client, repo_id, conv_id)).status_code == 201


async def test_invalid_questions_are_not_charged(limits, real_client, real_db_session) -> None:
    limits(ai_daily_limit_per_user=1, ai_burst_limit=100)
    repo_id, conv_id = await setup_member(real_client, real_db_session)
    assert (await ask(real_client, repo_id, conv_id, text="   ")).status_code == 422
    assert (await ask(real_client, repo_id, conv_id, text="x" * 3000)).status_code == 422
    assert (await ask(real_client, repo_id, 999999)).status_code == 404
    assert (await ask(real_client, repo_id, conv_id)).status_code == 201


async def test_streaming_questions_share_the_policy(limits, real_client, real_db_session, generator) -> None:
    limits(ai_daily_limit_per_user=1, ai_burst_limit=100)
    repo_id, conv_id = await setup_member(real_client, real_db_session)
    generator.error = GenerationError("unavailable", "down")
    failed = await ask(real_client, repo_id, conv_id, stream=True)
    assert "event: error" in failed.text  # streamed failure: nothing saved, so nothing charged
    generator.error = None
    assert "event: done" in (await ask(real_client, repo_id, conv_id, stream=True)).text
    refused = await ask(real_client, repo_id, conv_id, stream=True)
    assert refused.status_code == 429 and refused.headers["X-RateLimit-Scope"] == "daily"


async def test_global_budget_and_concurrency_protect_the_provider(limits, make_client, real_db_session, limiter) -> None:
    limits(ai_global_daily_limit=1)
    alice, a_user = await make_client("alice")
    bob, b_user = await make_client("bob")
    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id)
    for user in (a_user, b_user):
        real_db_session.add(UserRepository(user_id=user.id, repository_id=repo_id))
    await real_db_session.commit()
    assert (await ask(alice, repo_id, await conversation(alice, repo_id))).status_code == 201
    response = await ask(bob, repo_id, await conversation(bob, repo_id))
    assert response.status_code == 429 and response.headers["X-RateLimit-Scope"] == "global"

    limits(ai_global_concurrency=1)
    from apps.api.services.quota import GLOBAL_SLOT_KEY

    assert await limiter.acquire(GLOBAL_SLOT_KEY, 1, 60)  # another answer is being generated right now
    response = await ask(alice, repo_id, await conversation(alice, repo_id))
    assert response.status_code == 429 and response.headers["X-RateLimit-Scope"] == "busy"


async def test_the_limiter_being_down_fails_closed_for_questions(limits, real_client, real_db_session) -> None:
    limits()

    class Down:
        async def reserve(self, *a, **k):
            raise OSError("redis down")

    app.dependency_overrides[get_limiter] = lambda: Down()
    repo_id, conv_id = await setup_member(real_client, real_db_session)
    assert (await ask(real_client, repo_id, conv_id)).status_code == 503


# --- imports and background jobs --------------------------------------------------------------------------------

def metadata(n: int, name: str | None = None) -> GitHubRepositoryMetadata:
    return GitHubRepositoryMetadata(7_000_000 + n, name or f"octo/new{n}", "main", None)


async def test_hourly_import_limit(limits, real_client, github_service) -> None:
    limits(import_hourly_limit=2)
    statuses = []
    for n in range(3):
        github_service.get_repository_metadata.return_value = metadata(n)
        statuses.append(await real_client.post("/repositories", json={"github_url": f"https://github.com/octo/new{n}"}))
    assert [r.status_code for r in statuses] == [201, 201, 429]
    assert statuses[2].headers["X-RateLimit-Scope"] == "import" and "2 imports" in statuses[2].json()["detail"]
    assert int(statuses[2].headers["Retry-After"]) > 0


async def test_joining_a_stored_repository_and_bad_urls_do_not_use_the_import_limit(limits, real_client, real_db_session, github_service) -> None:
    limits(import_hourly_limit=1)
    stored = await new_repository(real_db_session, "octo/stored")
    for _ in range(3):  # already in the database (and already a member): no GitHub call, no quota
        response = await real_client.post("/repositories", json={"github_url": "https://github.com/octo/stored"})
        assert (response.status_code, response.json()["id"]) == (200, stored)
    github_service.get_repository_metadata.assert_not_called()
    for bad in ("https://evil.example/octo/x", "not a url", "https://github.com/a/b/../c", "https://github.com/../x"):
        assert (await real_client.post("/repositories", json={"github_url": bad})).status_code in (400, 422)
    github_service.get_repository_metadata.return_value = metadata(1)
    assert (await real_client.post("/repositories", json={"github_url": "https://github.com/octo/new1"})).status_code == 201


async def test_repository_cap_per_user(limits, real_client, github_service) -> None:
    limits(max_repositories_per_user=2)
    for n in range(2):
        github_service.get_repository_metadata.return_value = metadata(n)
        assert (await real_client.post("/repositories", json={"github_url": f"https://github.com/octo/new{n}"})).status_code == 201
    github_service.get_repository_metadata.return_value = metadata(5)
    response = await real_client.post("/repositories", json={"github_url": "https://github.com/octo/new5"})
    assert response.status_code == 403 and "limit of 2 repositories" in response.json()["detail"]
    # the cap applies to a user's own workspace only; the existing repositories are still usable
    assert len((await real_client.get("/repositories")).json()) == 2


async def test_concurrent_import_limit_per_user_and_duplicate_jobs(limits, real_client, real_db_session) -> None:
    limits(import_max_concurrent_per_user=1, import_hourly_limit=100)
    first, second = await new_repository(real_db_session, "octo/one"), await new_repository(real_db_session, "octo/two")
    assert (await real_client.post(f"/repositories/{first}/ingestions")).status_code == 202
    blocked = await real_client.post(f"/repositories/{second}/ingestions")
    assert blocked.status_code == 429 and blocked.headers["X-RateLimit-Scope"] == "import_concurrency"
    # the same repository again is a duplicate (409), not a second job and not a limit error
    assert (await real_client.post(f"/repositories/{first}/ingestions")).status_code == 409
    repo = await real_db_session.get(Repository, first)
    assert repo.ingestion_status == "queued"


async def test_failed_or_duplicate_ingestion_requests_give_their_hourly_unit_back(limits, real_client, real_db_session) -> None:
    limits(import_hourly_limit=2, import_max_concurrent_per_user=100)
    repo_id = await new_repository(real_db_session)
    assert (await real_client.post(f"/repositories/{repo_id}/ingestions")).status_code == 202
    for _ in range(5):
        assert (await real_client.post(f"/repositories/{repo_id}/ingestions")).status_code == 409  # never 429: refunded each time
    other = await new_repository(real_db_session, "octo/other")
    assert (await real_client.post(f"/repositories/{other}/ingestions")).status_code == 202
    third = await new_repository(real_db_session, "octo/third")
    assert (await real_client.post(f"/repositories/{third}/ingestions")).status_code == 429


async def test_architecture_and_embedding_jobs_have_hourly_limits(limits, real_client, real_db_session) -> None:
    limits(architecture_hourly_limit=1)
    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id)
    assert (await real_client.post(f"/repositories/{repo_id}/architecture")).status_code == 202
    second = await real_client.post(f"/repositories/{repo_id}/architecture?force=true")
    assert second.status_code in (202, 429)
    third = await real_client.post(f"/repositories/{repo_id}/architecture?force=true")
    assert third.status_code == 429 and third.headers["X-RateLimit-Scope"] == "architecture"


# --- guests, sign-in traffic, client addresses ------------------------------------------------------------------

async def test_guest_reads_are_limited_per_ip_and_signed_in_users_are_not(limits, anon_client, real_client, real_db_session) -> None:
    limits(guest_reads_per_minute=3, guest_showcase_repositories="octo/showcase")
    repo_id = await new_repository(real_db_session, "octo/showcase")
    await ingest(real_db_session, repo_id)
    codes = [(await anon_client.get(f"/repositories/{repo_id}/files")).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    # a forged forwarding header does not buy a fresh allowance
    forged = await anon_client.get(f"/repositories/{repo_id}/files", headers={"X-Forwarded-For": "203.0.113.9"})
    assert forged.status_code == 429 and forged.headers["X-RateLimit-Scope"] == "guest"
    for _ in range(6):
        assert (await real_client.get(f"/repositories/{repo_id}/files")).status_code == 200  # members are not metered here


def _request(peer: str, forwarded: str | None = None):
    headers = {"x-forwarded-for": forwarded} if forwarded else {}
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)


def test_forwarded_headers_are_believed_only_from_configured_proxies() -> None:
    assert client_ip(_request("198.51.100.5", "1.2.3.4"), make_settings()) == "198.51.100.5"  # no proxies configured
    proxied = make_settings(trusted_proxies="10.0.0.0/8, 192.0.2.1")
    assert client_ip(_request("198.51.100.5", "1.2.3.4"), proxied) == "198.51.100.5"  # peer is not a proxy: header ignored
    assert client_ip(_request("10.1.2.3", "203.0.113.7"), proxied) == "203.0.113.7"
    # a client cannot prepend a fake hop: hops are read from the right, skipping trusted proxies
    assert client_ip(_request("10.1.2.3", "9.9.9.9, 203.0.113.7, 10.9.9.9"), proxied) == "203.0.113.7"
    assert client_ip(_request("10.1.2.3", "garbage"), proxied) == "garbage"  # whatever the trusted proxy reported
    assert client_ip(_request("10.1.2.3"), proxied) == "10.1.2.3"


async def test_sign_in_traffic_is_limited_per_ip_and_reports_it_to_the_web_app(limits, anon_client) -> None:
    limits(auth_login_per_minute=2)
    settings = make_settings(auth_login_per_minute=2)
    app.dependency_overrides[settings_dep] = lambda: settings
    app.dependency_overrides[get_redis] = lambda: FakeRedis()
    app.dependency_overrides[oauth.get_oauth_client] = lambda: FakeOAuth(settings, identity())
    try:
        assert [(await anon_client.get("/auth/github/login")).status_code for _ in range(2)] == [303, 303]
        limited = await anon_client.get("/auth/github/login")
        assert limited.status_code == 303 and limited.headers["location"].endswith("/repositories?auth_error=rate_limited")
    finally:
        app.dependency_overrides.pop(get_redis, None)
        app.dependency_overrides.pop(oauth.get_oauth_client, None)
