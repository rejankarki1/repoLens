"""Atomic counters and leases for usage limits.

Every check-and-update runs as one Lua script inside Redis, so concurrent requests cannot both pass a limit that
only has room for one. `MemoryLimiter` has the same behaviour for tests that do not need a Redis server.

- `reserve(key, limit, window)`: fixed window. The window starts at the first reservation; the key expires by itself.
- `refund(key)`: gives a reservation back (never below zero).
- `acquire(key, limit, ttl)` / `release(key, token)`: a semaphore. A slot frees itself after `ttl`, so a crashed
  process cannot hold one forever.
"""

import asyncio
import secrets
import time
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis

_RESERVE = """
local limit, window, cost = tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
local used = tonumber(redis.call('GET', KEYS[1]) or '0')
if used + cost > limit then
  local ttl = redis.call('TTL', KEYS[1])
  if ttl < 0 then ttl = window end
  return {0, used, ttl}
end
used = redis.call('INCRBY', KEYS[1], cost)
if redis.call('TTL', KEYS[1]) < 0 then redis.call('EXPIRE', KEYS[1], window) end
return {1, used, redis.call('TTL', KEYS[1])}
"""

_REFUND = """
local cost = tonumber(ARGV[1])
local used = tonumber(redis.call('GET', KEYS[1]) or '0')
if used <= 0 then return 0 end
if used - cost <= 0 then redis.call('DEL', KEYS[1]) return 0 end
return redis.call('DECRBY', KEYS[1], cost)
"""

_ACQUIRE = """
local limit, ttl_ms, token = tonumber(ARGV[1]), tonumber(ARGV[2]), ARGV[3]
local t = redis.call('TIME')
local now = t[1] * 1000 + math.floor(t[2] / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZCARD', KEYS[1]) >= limit then return 0 end
redis.call('ZADD', KEYS[1], now + ttl_ms, token)
redis.call('PEXPIRE', KEYS[1], ttl_ms)
return 1
"""


@dataclass(frozen=True)
class Reservation:
    allowed: bool
    used: int
    retry_after: int  # seconds until the window resets (meaningful when not allowed)


class Limiter(Protocol):
    async def reserve(self, key: str, limit: int, window_seconds: int, cost: int = 1) -> Reservation: ...
    async def refund(self, key: str, cost: int = 1) -> None: ...
    async def usage(self, key: str) -> tuple[int, int]: ...
    async def acquire(self, key: str, limit: int, ttl_seconds: int) -> str | None: ...
    async def release(self, key: str, token: str) -> None: ...


class RedisLimiter:
    def __init__(self, redis: Redis, prefix: str = "rl:") -> None:
        self._r, self._p = redis, prefix
        self._reserve, self._refund, self._acquire = (redis.register_script(s) for s in (_RESERVE, _REFUND, _ACQUIRE))

    async def reserve(self, key: str, limit: int, window_seconds: int, cost: int = 1) -> Reservation:
        allowed, used, ttl = await self._reserve(keys=[self._p + key], args=[limit, window_seconds, cost])
        return Reservation(bool(allowed), int(used), max(1, int(ttl)))

    async def refund(self, key: str, cost: int = 1) -> None:
        await self._refund(keys=[self._p + key], args=[cost])

    async def usage(self, key: str) -> tuple[int, int]:
        """(used, seconds until the window resets) without counting anything; (0, 0) when no window is open."""
        used, ttl = await self._r.get(self._p + key), await self._r.ttl(self._p + key)
        return (int(used), max(0, int(ttl))) if used is not None and int(ttl) > 0 else (0, 0)

    async def acquire(self, key: str, limit: int, ttl_seconds: int) -> str | None:
        token = secrets.token_hex(8)
        got = await self._acquire(keys=[self._p + key], args=[limit, ttl_seconds * 1000, token])
        return token if got else None

    async def release(self, key: str, token: str) -> None:
        await self._r.zrem(self._p + key, token)


class MemoryLimiter:
    """The same semantics in process memory (one event loop, one lock). For tests."""

    def __init__(self, clock=time.monotonic) -> None:
        self._now = clock
        self._lock = asyncio.Lock()
        self._counters: dict[str, tuple[int, float]] = {}
        self._leases: dict[str, dict[str, float]] = {}

    async def reserve(self, key: str, limit: int, window_seconds: int, cost: int = 1) -> Reservation:
        async with self._lock:
            now = self._now()
            used, expires = self._counters.get(key, (0, 0.0))
            if expires <= now:
                used, expires = 0, now + window_seconds
            if used + cost > limit:
                return Reservation(False, used, max(1, int(expires - now + 0.999)))
            self._counters[key] = (used + cost, expires)
            return Reservation(True, used + cost, max(1, int(expires - now + 0.999)))

    async def refund(self, key: str, cost: int = 1) -> None:
        async with self._lock:
            used, expires = self._counters.get(key, (0, 0.0))
            if used > 0 and expires > self._now():
                self._counters[key] = (max(0, used - cost), expires)

    async def usage(self, key: str) -> tuple[int, int]:
        async with self._lock:
            used, expires = self._counters.get(key, (0, 0.0))
            now = self._now()
            return (used, max(1, int(expires - now + 0.999))) if used > 0 and expires > now else (0, 0)

    async def acquire(self, key: str, limit: int, ttl_seconds: int) -> str | None:
        async with self._lock:
            now = self._now()
            live = {t: e for t, e in self._leases.get(key, {}).items() if e > now}
            if len(live) >= limit:
                self._leases[key] = live
                return None
            token = secrets.token_hex(8)
            live[token] = now + ttl_seconds
            self._leases[key] = live
            return token

    async def release(self, key: str, token: str) -> None:
        async with self._lock:
            self._leases.get(key, {}).pop(token, None)
