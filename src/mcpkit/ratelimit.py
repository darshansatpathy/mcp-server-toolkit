"""Token-bucket rate limits per principal and tool class.

`MemoryLimiter` suits one replica. With several replicas, `RedisLimiter` keeps one bucket per key
across all of them, updated atomically by a Lua script.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import anyio


@dataclass(frozen=True)
class Decision:
    allowed: bool
    remaining: float
    retry_after: float


class Limiter(Protocol):
    async def hit(self, key: str, per_minute: int, cost: float = 1.0) -> Decision: ...


class MemoryLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic, max_keys: int = 100_000):
        self.clock, self.max_keys = clock, max_keys
        self.buckets: dict[str, tuple[float, float]] = {}
        self.lock = anyio.Lock()

    async def hit(self, key: str, per_minute: int, cost: float = 1.0) -> Decision:
        rate, capacity = per_minute / 60.0, float(per_minute)
        async with self.lock:
            now = self.clock()
            tokens, last = self.buckets.get(key, (capacity, now))
            tokens = min(capacity, tokens + (now - last) * rate)
            if tokens >= cost:
                tokens -= cost
                allowed, retry = True, 0.0
            else:
                allowed, retry = False, (cost - tokens) / rate
            if len(self.buckets) >= self.max_keys and key not in self.buckets:
                self.buckets.clear()  # bounded memory; a full bucket for everyone is the safe reset
            self.buckets[key] = (tokens, now)
            return Decision(allowed, tokens, retry)


_LUA = """
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local b = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(b[1]) or capacity
local ts = tonumber(b[2]) or now
tokens = math.min(capacity, tokens + math.max(0, now - ts) * rate)
local allowed = 0
local retry = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  retry = (cost - tokens) / rate
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', KEYS[1], math.ceil(capacity / rate) + 1)
return {allowed, tostring(tokens), tostring(retry)}
"""


class RedisLimiter:
    def __init__(self, client: Any, prefix: str = "mcpkit:rl:"):
        self.client, self.prefix = client, prefix
        self.script = client.register_script(_LUA)

    @classmethod
    def from_url(cls, url: str) -> RedisLimiter:
        import redis.asyncio as redis

        return cls(redis.from_url(url))

    async def hit(self, key: str, per_minute: int, cost: float = 1.0) -> Decision:
        allowed, tokens, retry = await self.script(keys=[self.prefix + key], args=[per_minute, per_minute / 60.0, cost])
        return Decision(bool(int(allowed)), float(tokens), float(retry))


def retry_header(d: Decision) -> str:
    return str(max(1, math.ceil(d.retry_after)))
