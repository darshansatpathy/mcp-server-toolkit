from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time

import pytest

from mcpkit.ratelimit import RedisLimiter

pytestmark = [pytest.mark.anyio, pytest.mark.redis]


@pytest.fixture(scope="module")
def redis_url():
    if os.environ.get("REDIS_URL"):
        yield os.environ["REDIS_URL"]
        return
    if not shutil.which("redis-server"):
        pytest.skip("no redis-server and no REDIS_URL")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    p = subprocess.Popen(["redis-server", "--port", str(port), "--save", "", "--appendonly", "no"], stdout=subprocess.DEVNULL)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.1).close()
            break
        except OSError:
            time.sleep(0.05)
    yield f"redis://127.0.0.1:{port}/0"
    p.terminate()
    p.wait()


async def test_shared_bucket_across_replicas(redis_url):
    """Two limiter instances (two replicas) draw from one bucket."""
    a, b = RedisLimiter.from_url(redis_url), RedisLimiter.from_url(redis_url)
    key = f"t:{time.time_ns()}"
    hits = [await (a if i % 2 else b).hit(key, 10) for i in range(12)]
    assert [h.allowed for h in hits] == [True] * 10 + [False] * 2
    assert 5.0 < hits[-1].retry_after <= 6.0  # 10/min: one token every 6 s
    await a.client.aclose()
    await b.client.aclose()


async def test_refill(redis_url):
    lim = RedisLimiter.from_url(redis_url)
    key = f"r:{time.time_ns()}"
    assert all([(await lim.hit(key, 60)).allowed for _ in range(60)])
    assert not (await lim.hit(key, 60)).allowed
    time.sleep(1.1)  # 60/min is one token a second
    assert (await lim.hit(key, 60)).allowed
    await lim.client.aclose()
