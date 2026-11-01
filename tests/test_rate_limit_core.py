"""The limiter primitives, against real Redis (Lua scripts) and the in-memory twin. Concurrency must not beat a limit."""

import asyncio
import uuid

import pytest
from fastapi import HTTPException
from redis.asyncio import Redis

from apps.api.config import get_settings
from apps.api.services.quota import GLOBAL_DAY_KEY, reserve_ai_question
from apps.api.services.rate_limit import MemoryLimiter, RedisLimiter
from tests.auth_support import make_settings

pytestmark = pytest.mark.anyio


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
async def redis_client():
    client = Redis.from_url(get_settings().redis_url, socket_connect_timeout=2, max_connections=500)
    try:
        await client.ping()
    except Exception as exc:  # the CI workflow and local docker-compose both provide Redis
        pytest.fail(f"These tests need a Redis server at REDIS_URL: {exc}")
    yield client
    await client.aclose()


@pytest.fixture(params=["redis", "memory"])
async def limiter(request, redis_client):
    """Each primitive runs against both implementations. Redis keys are namespaced per test and removed afterwards."""
    if request.param == "memory":
        yield MemoryLimiter()
        return
    prefix = f"test:{uuid.uuid4().hex}:"
    yield RedisLimiter(redis_client, prefix=prefix)
    async for key in redis_client.scan_iter(match=prefix + "*"):
        await redis_client.delete(key)


async def test_reserve_allows_up_to_the_limit_then_refuses_with_a_retry_time(limiter) -> None:
    results = [await limiter.reserve("k", 3, 60) for _ in range(5)]
    assert [r.allowed for r in results] == [True, True, True, False, False]
    assert [r.used for r in results[:3]] == [1, 2, 3]
    assert 1 <= results[3].retry_after <= 60


async def test_the_window_starts_at_the_first_use_and_then_resets(redis_client) -> None:
    clock = Clock()
    memory = MemoryLimiter(clock)
    redis = RedisLimiter(redis_client, prefix=f"test:{uuid.uuid4().hex}:")
    assert (await memory.reserve("k", 1, 10)).allowed and not (await memory.reserve("k", 1, 10)).allowed
    clock.now += 11
    assert (await memory.reserve("k", 1, 10)).allowed
    assert (await redis.reserve("k", 1, 1)).allowed and not (await redis.reserve("k", 1, 1)).allowed
    await asyncio.sleep(1.2)
    assert (await redis.reserve("k", 1, 1)).allowed
    await redis_client.delete(redis._p + "k")


async def test_refund_gives_capacity_back_and_never_goes_below_zero(limiter) -> None:
    for _ in range(3):
        await limiter.reserve("k", 3, 60)
    assert not (await limiter.reserve("k", 3, 60)).allowed
    await limiter.refund("k")
    assert (await limiter.reserve("k", 3, 60)).allowed
    for _ in range(10):  # more refunds than reservations
        await limiter.refund("k")
    allowed = [(await limiter.reserve("k", 3, 60)).allowed for _ in range(5)]
    assert allowed == [True, True, True, False, False]  # the limit still holds after the over-refund


async def test_concurrent_reservations_cannot_exceed_the_limit(limiter) -> None:
    results = await asyncio.gather(*(limiter.reserve("k", 20, 60) for _ in range(120)))
    assert sum(r.allowed for r in results) == 20


async def test_concurrent_reserve_and_refund_keep_the_count_consistent(limiter) -> None:
    await asyncio.gather(*(limiter.reserve("k", 1000, 60) for _ in range(50)))
    await asyncio.gather(*(limiter.refund("k") for _ in range(20)))
    assert (await limiter.reserve("k", 1000, 60)).used == 31


async def test_leases_are_a_semaphore_that_concurrency_cannot_beat(limiter) -> None:
    tokens = await asyncio.gather(*(limiter.acquire("slots", 8, 30) for _ in range(40)))
    held = [t for t in tokens if t]
    assert len(held) == 8 and len(set(held)) == 8
    assert await limiter.acquire("slots", 8, 30) is None
    await limiter.release("slots", held[0])
    assert await limiter.acquire("slots", 8, 30) is not None


async def test_a_lease_frees_itself_if_its_holder_dies(redis_client) -> None:
    redis = RedisLimiter(redis_client, prefix=f"test:{uuid.uuid4().hex}:")
    assert await redis.acquire("slots", 1, 1) and await redis.acquire("slots", 1, 1) is None
    await asyncio.sleep(1.3)
    assert await redis.acquire("slots", 1, 1) is not None
    await redis_client.delete(redis._p + "slots")
    clock, memory = Clock(), None
    memory = MemoryLimiter(clock)
    assert await memory.acquire("s", 1, 5) and await memory.acquire("s", 1, 5) is None
    clock.now += 6
    assert await memory.acquire("s", 1, 5) is not None


# --- the AI question policy, on the primitives ------------------------------------------------------------------

async def refused(limiter, settings, user: int = 1) -> HTTPException:
    with pytest.raises(HTTPException) as caught:
        await reserve_ai_question(limiter, settings, user)
    return caught.value


async def test_ai_policy_concurrent_questions_cannot_exceed_the_daily_quota(limiter) -> None:
    settings = make_settings(ai_daily_limit_per_user=20, ai_burst_limit=1000)
    outcomes = await asyncio.gather(*(reserve_ai_question(limiter, settings, 7) for _ in range(80)), return_exceptions=True)
    granted = [o for o in outcomes if not isinstance(o, Exception)]
    assert len(granted) == 20
    assert all(isinstance(o, HTTPException) and o.status_code == 429 and o.headers["X-RateLimit-Scope"] == "daily" for o in outcomes if isinstance(o, Exception))


async def test_ai_policy_burst_limit_is_a_429_with_scope_and_retry_time(limiter) -> None:
    settings = make_settings(ai_burst_limit=2)
    for _ in range(2):
        await (await reserve_ai_question(limiter, settings, 1)).finish(True)
    error = await refused(limiter, settings)
    assert (error.status_code, error.headers["X-RateLimit-Scope"], int(error.headers["Retry-After"]) > 0) == (429, "burst", True)
    assert "too quickly" in error.detail


async def test_ai_policy_daily_limit_is_per_user(limiter) -> None:
    settings = make_settings(ai_daily_limit_per_user=1)
    await (await reserve_ai_question(limiter, settings, 2)).finish(True)
    error = await refused(limiter, settings, 2)
    assert error.headers["X-RateLimit-Scope"] == "daily" and "questions for today" in error.detail
    await (await reserve_ai_question(limiter, settings, 3)).finish(True)  # somebody else is unaffected


async def test_ai_policy_global_budget_is_shared_by_everyone(limiter) -> None:
    settings = make_settings(ai_global_daily_limit=2)
    await (await reserve_ai_question(limiter, settings, 3)).finish(True)
    await (await reserve_ai_question(limiter, settings, 4)).finish(True)
    error = await refused(limiter, settings, 5)
    assert error.headers["X-RateLimit-Scope"] == "global" and "AI budget" in error.detail


async def test_ai_policy_concurrency_slots_come_back_when_an_answer_ends(limiter) -> None:
    settings = make_settings(ai_global_concurrency=1)
    held = await reserve_ai_question(limiter, settings, 6)
    assert (await refused(limiter, settings, 7)).headers["X-RateLimit-Scope"] == "busy"
    await held.finish(True)
    await (await reserve_ai_question(limiter, settings, 7)).finish(True)


async def test_ai_policy_failed_answers_are_refunded_but_burst_attempts_are_not(limiter) -> None:
    settings = make_settings(ai_daily_limit_per_user=2, ai_global_daily_limit=2, ai_burst_limit=3)
    for _ in range(3):
        await (await reserve_ai_question(limiter, settings, 1)).finish(False)  # three failures cost no daily quota ...
    error = await refused(limiter, settings, 1)
    assert error.headers["X-RateLimit-Scope"] == "burst"  # ... but the burst counted every attempt
    other = make_settings(ai_daily_limit_per_user=2, ai_global_daily_limit=2)
    await (await reserve_ai_question(limiter, other, 9)).finish(True)
    await (await reserve_ai_question(limiter, other, 9)).finish(True)
    assert (await refused(limiter, other, 9)).headers["X-RateLimit-Scope"] == "daily"
    assert (await refused(limiter, other, 10)).headers["X-RateLimit-Scope"] == "global"  # the shared budget is spent


async def test_ai_policy_a_refused_question_leaves_no_reservation_behind(limiter) -> None:
    settings = make_settings(ai_global_daily_limit=1, ai_daily_limit_per_user=5)
    await (await reserve_ai_question(limiter, settings, 1)).finish(True)
    for _ in range(3):
        assert (await refused(limiter, settings, 2)).headers["X-RateLimit-Scope"] == "global"
    # user 2 never consumed their own daily quota while refused
    roomy = make_settings(ai_global_daily_limit=100, ai_daily_limit_per_user=5)
    for _ in range(5):
        await (await reserve_ai_question(limiter, roomy, 2)).finish(True)
    assert GLOBAL_DAY_KEY


async def test_ai_policy_fails_closed_when_redis_is_unavailable() -> None:
    class Broken:
        async def reserve(self, *a, **k):
            raise OSError("down")

    with pytest.raises(HTTPException) as caught:
        await reserve_ai_question(Broken(), make_settings(), 1)  # type: ignore[arg-type]
    assert caught.value.status_code == 503
