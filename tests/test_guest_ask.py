"""Live Ask AI for visitors: showcase only, stateless, capped server-side, fail-closed, never refunded after paid work."""

import json
from unittest.mock import MagicMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from apps.api.core.auth import settings_dep
from apps.api.core.limits import get_limiter, trustworthy_client_ip
from apps.api.core.queue import get_queue
from apps.api.main import app
from apps.api.models.entities import Conversation, Message, Repository, UserRepository
from apps.api.services.embeddings import get_embedder
from apps.api.services.generation import GenerationError, get_generator
from apps.api.services.quota import GUEST_GLOBAL_KEY, GLOBAL_DAY_KEY, GLOBAL_SLOT_KEY, GuestAiReservation
from apps.api.services.rate_limit import MemoryLimiter
from tests.auth_support import WEB, make_settings
from tests.search_support import FakeEmbedder, ingest, new_repository
from tests.test_rag_api import FakeGenerator

pytestmark = pytest.mark.anyio

SHOWCASE = "octo/showcase"
VISITOR_A, VISITOR_B, VISITOR_C = "a" * 32, "b" * 32, "c" * 32
ORIGIN = {"Origin": WEB}
QUESTION = {"content": "How are passwords hashed?"}


class Env:
    """Per-test knobs: the settings and the fakes the app is wired to."""

    def __init__(self) -> None:
        self.settings = None
        self.generator = FakeGenerator()
        self.embedder = FakeEmbedder()
        self.use_generator = True

    def configure(self, **overrides) -> None:
        values = dict(guest_showcase_repositories=SHOWCASE, guest_ai_enabled=True, guest_ai_per_visitor_daily=3,
                      guest_ai_per_ip_daily=30, guest_ai_global_daily=20)
        values.update(overrides)
        self.settings = make_settings(**values)


@pytest.fixture
def env(no_auto_membership, app_overrides):
    state = Env()
    state.configure()
    app.dependency_overrides[settings_dep] = lambda: state.settings
    app.dependency_overrides[get_queue] = lambda: MagicMock()
    app.dependency_overrides[get_generator] = lambda: state.generator if state.use_generator else None
    app.dependency_overrides[get_embedder] = lambda: state.embedder
    yield state
    for dep in (settings_dep, get_queue, get_generator, get_embedder):
        app.dependency_overrides.pop(dep, None)


async def showcase_repo(db: AsyncSession, name: str = SHOWCASE) -> int:
    repo_id = await new_repository(db, name)
    await ingest(db, repo_id)
    return repo_id


def cookie(visitor: str, name: str = "rl_visitor") -> dict[str, str]:
    return {"Cookie": f"{name}={visitor}"}


def events(response) -> list[tuple[str, dict]]:
    out, event = [], None
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: ") and event:
            out.append((event, json.loads(line[6:])))
    return out


async def ask(client, repo_id: int, visitor: str | None = VISITOR_A, headers: dict | None = None, body: dict = QUESTION):
    merged = {**ORIGIN, **(cookie(visitor) if visitor else {}), **(headers or {})}
    return await client.post(f"/repositories/{repo_id}/guest-ask/stream", json=body, headers=merged)


async def used(limiter: MemoryLimiter, key: str) -> int:
    return (await limiter.usage(key))[0]


# --- it answers, with sources, and keeps nothing -----------------------------------------------------------------

async def test_a_visitor_gets_a_streamed_answer_with_validated_citations_and_nothing_is_saved(env, anon_client, real_db_session, limiter) -> None:
    repo_id = await showcase_repo(real_db_session)

    response = await ask(anon_client, repo_id)

    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    seen = events(response)
    assert [name for name, _ in seen][0] == "retrieval" and seen[-1][0] == "done"
    done = seen[-1][1]
    assert done["assistant_message"]["content"].startswith("Passwords are hashed") and done["citations"]
    assert all(c["path"] for c in done["citations"])
    assert len(env.generator.calls) == 1
    for model in (Conversation, Message, UserRepository):
        assert (await real_db_session.execute(select(func.count()).select_from(model))).scalar_one() == 0, model.__name__
    assert await used(limiter, f"guestai:visitor:{VISITOR_A}") == 1 and await used(limiter, GUEST_GLOBAL_KEY) == 1


async def test_the_visitor_cookie_is_issued_httponly_samesite_lax_and_secure_in_production(env, anon_client, real_db_session) -> None:
    repo_id = await showcase_repo(real_db_session)

    dev = (await ask(anon_client, repo_id, visitor=None)).headers["set-cookie"].lower()
    assert dev.startswith("rl_visitor=") and "httponly" in dev and "samesite=lax" in dev and "secure" not in dev and "path=/" in dev

    env.configure(environment="production", database_url="postgresql+asyncpg://u:p@db.example.com/x")
    prod = (await ask(anon_client, repo_id, visitor=None)).headers["set-cookie"].lower()
    assert prod.startswith("__host-rl_visitor=") and "secure" in prod and "httponly" in prod and "samesite=lax" in prod
    assert "domain" not in prod  # __Host- cookies cannot carry one


async def test_a_malformed_visitor_cookie_is_replaced_not_trusted(env, anon_client, real_db_session, limiter) -> None:
    repo_id = await showcase_repo(real_db_session)

    response = await ask(anon_client, repo_id, visitor="../../ai:global:day")

    assert response.status_code == 200
    new_id = response.headers["set-cookie"].split("=")[1].split(";")[0]
    assert len(new_id) == 32 and await used(limiter, f"guestai:visitor:{new_id}") == 1
    assert await used(limiter, GLOBAL_DAY_KEY) == 1  # only the legitimate shared-budget counter moved


# --- only the allow-listed repositories, only when enabled ---------------------------------------------------------

async def test_it_is_off_by_default(env, anon_client, real_db_session, limiter) -> None:
    repo_id = await showcase_repo(real_db_session)
    env.configure(guest_ai_enabled=False)

    assert (await ask(anon_client, repo_id)).status_code == 404
    status = (await anon_client.get(f"/repositories/{repo_id}/guest-ask")).json()
    assert status["enabled"] is False and status["remaining"] == 0
    assert env.generator.calls == [] and await used(limiter, GUEST_GLOBAL_KEY) == 0
    assert not Settings_default_enabled()


def Settings_default_enabled() -> bool:
    from apps.api.config import Settings

    return Settings(_env_file=None).guest_ai_enabled


async def test_repositories_outside_the_showcase_are_never_reachable_by_anyone(env, anon_client, make_client, real_db_session, limiter) -> None:
    private = await showcase_repo(real_db_session, "octo/private")
    alice, a_user = await make_client("alice")
    real_db_session.add(UserRepository(user_id=a_user.id, repository_id=private))
    await real_db_session.commit()

    for client in (anon_client, alice):  # a visitor, and even the repository's own member
        assert (await ask(client, private)).status_code == 404
        assert (await client.get(f"/repositories/{private}/guest-ask")).json()["enabled"] is False
    assert (await ask(anon_client, 999_999)).status_code == 404
    assert env.generator.calls == [] and await used(limiter, GUEST_GLOBAL_KEY) == 0


async def test_visitors_remain_read_only_everywhere_else(env, anon_client, real_db_session) -> None:
    repo_id = await showcase_repo(real_db_session)
    await ask(anon_client, repo_id)
    attempts = [
        ("POST", "/repositories", {"json": {"github_url": "https://github.com/octo/x"}}),
        ("POST", f"/repositories/{repo_id}/ingestions", {}),
        ("POST", f"/repositories/{repo_id}/embeddings", {}),
        ("POST", f"/repositories/{repo_id}/architecture", {}),
        ("DELETE", f"/repositories/{repo_id}", {}),
        ("POST", f"/repositories/{repo_id}/conversations", {}),
        ("POST", f"/repositories/{repo_id}/conversations/1/messages/stream", {"json": QUESTION}),
    ]
    for method, path, kwargs in attempts:
        assert (await anon_client.request(method, path, headers=ORIGIN, **kwargs)).status_code == 401, (method, path)
    assert await real_db_session.get(Repository, repo_id) is not None


# --- origin, input, configuration ------------------------------------------------------------------------------------

@pytest.mark.parametrize("origin", [None, "https://evil.example", "null", WEB + ".evil.example"])
async def test_a_missing_or_foreign_origin_is_refused_before_anything_is_counted(env, anon_client, real_db_session, limiter, origin) -> None:
    repo_id = await showcase_repo(real_db_session)
    headers = {"Origin": origin} if origin else {}

    response = await anon_client.post(f"/repositories/{repo_id}/guest-ask/stream", json=QUESTION, headers={**headers, **cookie(VISITOR_A)})

    assert response.status_code == 403
    assert env.generator.calls == [] and await used(limiter, f"guestai:visitor:{VISITOR_A}") == 0


async def test_invalid_questions_and_a_missing_generator_cost_nothing(env, anon_client, real_db_session, limiter) -> None:
    repo_id = await showcase_repo(real_db_session)

    assert (await ask(anon_client, repo_id, body={"content": "   "})).status_code == 422
    env.use_generator = False
    assert (await ask(anon_client, repo_id)).status_code == 503
    assert await used(limiter, f"guestai:visitor:{VISITOR_A}") == 0 and await used(limiter, GUEST_GLOBAL_KEY) == 0


# --- the caps ----------------------------------------------------------------------------------------------------------

async def test_a_visitor_gets_three_questions_a_day_and_others_are_unaffected(env, anon_client, real_db_session, limiter) -> None:
    repo_id = await showcase_repo(real_db_session)

    for _ in range(3):
        assert (await ask(anon_client, repo_id)).status_code == 200
    fourth = await ask(anon_client, repo_id)

    assert fourth.status_code == 429 and fourth.headers["x-ratelimit-scope"] == "guest_visitor"
    assert int(fourth.headers["retry-after"]) > 0 and "free questions for today" in fourth.json()["detail"]
    assert len(env.generator.calls) == 3
    assert (await ask(anon_client, repo_id, visitor=VISITOR_B)).status_code == 200
    status = (await anon_client.get(f"/repositories/{repo_id}/guest-ask", headers=cookie(VISITOR_A))).json()
    assert status["enabled"] and status["limit"] == 3 and status["remaining"] == 0 and status["resets_in_seconds"] > 0


async def test_all_guests_share_a_daily_cap_and_a_refused_question_gives_back_what_it_reserved(env, anon_client, real_db_session, limiter) -> None:
    repo_id = await showcase_repo(real_db_session)
    env.configure(guest_ai_global_daily=2)

    await ask(anon_client, repo_id, visitor=VISITOR_A)
    await ask(anon_client, repo_id, visitor=VISITOR_B)
    refused = await ask(anon_client, repo_id, visitor=VISITOR_C)

    assert refused.status_code == 429 and refused.headers["x-ratelimit-scope"] == "guest_global"
    assert await used(limiter, f"guestai:visitor:{VISITOR_C}") == 0  # the visitor counter was returned
    assert await used(limiter, GLOBAL_DAY_KEY) == 2  # and so was nothing extra taken from the shared budget


async def test_the_shared_ai_budget_still_applies_and_guest_counters_are_returned(env, anon_client, real_db_session, limiter) -> None:
    repo_id = await showcase_repo(real_db_session)
    env.configure(ai_global_daily_limit=1)

    assert (await ask(anon_client, repo_id, visitor=VISITOR_A)).status_code == 200
    refused = await ask(anon_client, repo_id, visitor=VISITOR_B)

    assert refused.status_code == 429 and refused.headers["x-ratelimit-scope"] == "global"
    assert await used(limiter, f"guestai:visitor:{VISITOR_B}") == 0 and await used(limiter, GUEST_GLOBAL_KEY) == 1


async def test_the_shared_concurrency_limit_applies_and_the_slot_is_freed_after_each_answer(env, anon_client, real_db_session, limiter) -> None:
    repo_id = await showcase_repo(real_db_session)
    env.configure(ai_global_concurrency=1)

    assert (await ask(anon_client, repo_id, visitor=VISITOR_A)).status_code == 200
    assert (await ask(anon_client, repo_id, visitor=VISITOR_B)).status_code == 200  # the first answer released its slot
    held = await limiter.acquire(GLOBAL_SLOT_KEY, 1, 60)
    assert held is not None
    busy = await ask(anon_client, repo_id, visitor=VISITOR_C)
    assert busy.status_code == 429 and busy.headers["x-ratelimit-scope"] == "busy"
    assert await used(limiter, f"guestai:visitor:{VISITOR_C}") == 0


# --- per-address caps are used only when the address can be believed -------------------------------------------------

def request_from(peer: str, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "headers": headers, "client": (peer, 5000)})


def test_a_production_server_without_trusted_proxies_has_no_trustworthy_address() -> None:
    prod = make_settings(environment="production", database_url="postgresql+asyncpg://u:p@db.example.com/x")
    assert trustworthy_client_ip(request_from("10.1.2.3", "203.0.113.9"), prod) is None  # the peer is Render's proxy, and XFF is unproven


def test_development_uses_the_peer_and_never_the_forwarded_header() -> None:
    assert trustworthy_client_ip(request_from("127.0.0.1", "203.0.113.9"), make_settings()) == "127.0.0.1"


def test_with_trusted_proxies_the_first_unvouched_hop_is_the_client_and_spoofing_is_ignored() -> None:
    settings = make_settings(environment="production", database_url="postgresql+asyncpg://u:p@db.example.com/x", trusted_proxies="10.0.0.0/8")
    assert trustworthy_client_ip(request_from("10.1.2.3", "198.51.100.1, 203.0.113.9, 10.9.9.9"), settings) == "203.0.113.9"  # the left side is client-supplied
    assert trustworthy_client_ip(request_from("192.0.2.50", "203.0.113.9"), settings) == "192.0.2.50"  # not from a proxy: forged
    assert trustworthy_client_ip(request_from("10.1.2.3", "10.5.5.5"), settings) is None  # only proxies in the chain


async def test_the_per_address_cap_counts_the_real_client_behind_a_trusted_proxy(env, anon_client, real_db_session, limiter) -> None:
    repo_id = await showcase_repo(real_db_session)
    env.configure(trusted_proxies="127.0.0.1", guest_ai_per_ip_daily=2)
    xff = {"X-Forwarded-For": "203.0.113.7"}

    await ask(anon_client, repo_id, visitor=VISITOR_A, headers=xff)
    await ask(anon_client, repo_id, visitor=VISITOR_B, headers=xff)  # a fresh cookie does not help
    refused = await ask(anon_client, repo_id, visitor=VISITOR_C, headers=xff)

    assert refused.status_code == 429 and refused.headers["x-ratelimit-scope"] == "guest_ip"
    assert (await ask(anon_client, repo_id, visitor=VISITOR_C, headers={"X-Forwarded-For": "203.0.113.8"})).status_code == 200


async def test_without_a_trustworthy_address_only_the_visitor_and_global_caps_apply(env, anon_client, real_db_session, limiter) -> None:
    repo_id = await showcase_repo(real_db_session)
    env.configure(environment="production", database_url="postgresql+asyncpg://u:p@db.example.com/x", guest_ai_per_ip_daily=1)
    spoofed = {"X-Forwarded-For": "203.0.113.7", **cookie(VISITOR_A, "__Host-rl_visitor")}

    for _ in range(2):  # the per-address cap of 1 would have refused the second, were an address believed
        response = await anon_client.post(f"/repositories/{repo_id}/guest-ask/stream", json=QUESTION, headers={**ORIGIN, **spoofed})
        assert response.status_code == 200
    assert await used(limiter, "guestai:ip:203.0.113.7") == 0 and await used(limiter, "guestai:ip:127.0.0.1") == 0


# --- fail closed, and no refunds once paid work may have happened ------------------------------------------------------

class BrokenLimiter(MemoryLimiter):
    async def reserve(self, *args, **kwargs):
        raise OSError("redis down")

    async def usage(self, key):
        raise OSError("redis down")


async def test_when_redis_cannot_be_asked_the_question_is_refused_and_no_model_runs(env, anon_client, real_db_session) -> None:
    repo_id = await showcase_repo(real_db_session)
    app.dependency_overrides[get_limiter] = lambda: BrokenLimiter()

    response = await ask(anon_client, repo_id)

    assert response.status_code == 503 and env.generator.calls == [] and env.embedder.query_calls == 0
    assert (await anon_client.get(f"/repositories/{repo_id}/guest-ask")).status_code == 503  # the display fails closed too


async def test_a_repository_that_is_not_indexed_is_refused_before_any_retrieval_and_the_quota_is_returned(env, anon_client, real_db_session, limiter) -> None:
    repo_id = await new_repository(real_db_session, SHOWCASE)  # stored, but never ingested: no chunks

    response = await ask(anon_client, repo_id)

    assert response.status_code == 409
    assert env.embedder.query_calls == 0 and env.generator.calls == []
    assert await used(limiter, f"guestai:visitor:{VISITOR_A}") == 0 and await used(limiter, GUEST_GLOBAL_KEY) == 0
    assert await used(limiter, GLOBAL_DAY_KEY) == 0


async def test_a_failed_generation_is_not_refunded_because_retrieval_already_ran(env, anon_client, real_db_session, limiter) -> None:
    repo_id = await showcase_repo(real_db_session)
    env.generator = FakeGenerator(error=GenerationError("unavailable", "The model is unavailable"))

    response = await ask(anon_client, repo_id)

    assert response.status_code == 200 and events(response)[-1][0] == "error"
    assert len(env.generator.calls) == 1  # the model was reached
    assert await used(limiter, f"guestai:visitor:{VISITOR_A}") == 1 and await used(limiter, GUEST_GLOBAL_KEY) == 1
    assert await used(limiter, GLOBAL_DAY_KEY) == 1
    assert await limiter.acquire(GLOBAL_SLOT_KEY, 1, 60) is not None  # the slot is free again


async def test_an_abandoned_stream_keeps_the_counters_and_frees_the_slot(limiter) -> None:
    """The client going away cancels the generator; that must not give the paid question back."""
    keys = ["guestai:visitor:x", GUEST_GLOBAL_KEY, GLOBAL_DAY_KEY]
    for key in keys:
        await limiter.reserve(key, 5, 3600)
    token = await limiter.acquire(GLOBAL_SLOT_KEY, 1, 60)
    reservation = GuestAiReservation(limiter, keys, token)

    from apps.api.routes.guest_ask import _guarded

    async def endless():
        yield "event: retrieval\ndata: {}\n\n"
        yield "event: token\ndata: {}\n\n"

    stream = _guarded(endless(), reservation)
    await stream.__anext__()
    await stream.aclose()  # what Starlette does when the connection drops

    for key in keys:
        assert await used(limiter, key) == 1, key
    assert await limiter.acquire(GLOBAL_SLOT_KEY, 1, 60) is not None
    await reservation.refund_unused()  # and a late "refund" after finishing is a no-op
    assert await used(limiter, keys[0]) == 1


async def test_only_a_reservation_that_never_reached_paid_work_can_be_refunded(limiter) -> None:
    keys = ["guestai:visitor:y", GUEST_GLOBAL_KEY]
    for key in keys:
        await limiter.reserve(key, 5, 3600)
    reservation = GuestAiReservation(limiter, keys, await limiter.acquire(GLOBAL_SLOT_KEY, 1, 60))

    await reservation.refund_unused()

    assert [await used(limiter, key) for key in keys] == [0, 0]
    assert await limiter.acquire(GLOBAL_SLOT_KEY, 1, 60) is not None
