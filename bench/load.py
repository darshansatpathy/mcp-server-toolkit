"""Load test over Streamable HTTP with real tokens: throughput, latency, and that limits hold under load.

python bench/load.py --clients 32 --calls 100 --out docs/performance.md
"""

from __future__ import annotations

import argparse
import random
import socket
import statistics
import tempfile
import threading
import time
from pathlib import Path

import anyio
import httpx2
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from mcpkit import devkeys
from mcpkit.auth import JWTVerifier, StaticKeys
from mcpkit.config import Settings
from mcpkit.ratelimit import MemoryLimiter
from mcpkit.server import http_app
from mcpkit.store import Store


def start(settings: Settings) -> tuple[uvicorn.Server, threading.Thread, int]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    verifier = JWTVerifier(StaticKeys.from_file(Path(str(settings.jwks_file))), settings.issuer, settings.audience, settings.algorithms)
    app = http_app(settings, Store(settings.database), limiter=MemoryLimiter(), verifier=verifier)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.02)
    return server, t, port


async def worker(url: str, token: str, calls: int, seed: int, lat: dict[str, list[float]], errors: list[str]) -> None:
    rnd = random.Random(seed)
    async with (
        httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30) as h,
        Client(streamable_http_client(url, http_client=h)) as c,
    ):
        ids: list[str] = []
        for i in range(calls):
            roll = rnd.random()
            if roll < 0.2 or not ids:
                name, args = "create_ticket", {"title": f"Load ticket {seed}-{i}", "body": "Sync fails on the third device after upgrade."}
            elif roll < 0.6:
                name, args = "get_ticket", {"ticket_id": rnd.choice(ids)}
            else:
                name, args = "search_tickets", {"query": rnd.choice(["sync", "device", "upgrade", "third"]), "limit": 10}
            t0 = time.perf_counter()
            r = await c.call_tool(name, args)
            lat.setdefault(name, []).append(time.perf_counter() - t0)
            if r.is_error:
                errors.append(f"{name}: {r.content[0].text}")
            elif name == "create_ticket":
                ids.append(r.structured_content["ticket"]["id"])


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(p * (len(xs) - 1)))] * 1000


async def limited(url: str, token: str, n: int) -> tuple[int, int]:
    async with (
        httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30) as h,
        Client(streamable_http_client(url, http_client=h)) as c,
    ):
        results = [await c.call_tool("search_tickets", {}) for _ in range(n)]
    refused = sum(1 for r in results if r.is_error and "Rate limited" in r.content[0].text)
    return n - refused, refused


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clients", type=int, default=32)
    ap.add_argument("--calls", type=int, default=100)
    ap.add_argument("--out")
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as d:
        private, jwks = devkeys.generate(Path(d))
        base = Settings(jwks_file=jwks, database=Path(d) / "load.db", issuer="https://idp.bench", audience="http://127.0.0.1/mcp")
        s = base.model_copy(update={"rate_per_minute": 100_000, "write_rate_per_minute": 100_000})
        server, thread, port = start(s)
        url = f"http://127.0.0.1:{port}/mcp"
        key = private.read_bytes()
        tokens = [
            devkeys.mint(
                key,
                issuer=s.issuer,
                audience=s.audience,
                subject=f"user{i}",
                tenant=f"tenant{i % 4}",
                scopes=["tickets:read", "tickets:write"],
            )
            for i in range(a.clients)
        ]
        lat: dict[str, list[float]] = {}
        errors: list[str] = []

        async def run() -> float:
            t0 = time.perf_counter()
            async with anyio.create_task_group() as tg:
                for i in range(a.clients):
                    tg.start_soon(worker, url, tokens[i], a.calls, i, lat, errors)
            return time.perf_counter() - t0

        elapsed = anyio.run(run)
        server.should_exit = True
        thread.join(5)

        # Limits under load: a second server with the default limit (120 reads a minute).
        server, thread, port = start(base.model_copy(update={"database": Path(d) / "limit.db"}))
        tok = devkeys.mint(key, issuer=s.issuer, audience=s.audience, subject="burst", tenant="t", scopes=["tickets:read"])
        allowed, refused = anyio.run(limited, f"http://127.0.0.1:{port}/mcp", tok, 200)
        server.should_exit = True
        thread.join(5)

    total = sum(len(v) for v in lat.values())
    lines = [
        "# Performance",
        "",
        f"Generated by `python bench/load.py --clients {a.clients} --calls {a.calls}`: one uvicorn worker, SQLite in WAL mode, "
        "Streamable HTTP in stateless JSON mode, every call carrying an RS256 token verified on the request. "
        f"{a.clients} concurrent clients across 4 tenants; 20% creates, 40% gets, 40% full-text searches.",
        "",
        f"**{total} tool calls in {elapsed:.1f} s: {total / elapsed:,.0f} calls/s**, {len(errors)} errors.",
        "",
        "| Tool | Calls | p50 ms | p95 ms | p99 ms |",
        "|---|---|---|---|---|",
    ]
    every = [x for v in lat.values() for x in v]
    for name in sorted(lat):
        v = lat[name]
        lines.append(f"| {name} | {len(v)} | {pct(v, 0.5):.1f} | {pct(v, 0.95):.1f} | {pct(v, 0.99):.1f} |")
    lines.append(f"| all | {total} | {pct(every, 0.5):.1f} | {pct(every, 0.95):.1f} | {pct(every, 0.99):.1f} |")
    lines += [
        "",
        f"Mean {statistics.mean(every) * 1000:.1f} ms. Latency here includes the client's own work; all of it ran on one machine.",
        "",
        "## Limits hold under a burst",
        "",
        f"One principal sending 200 reads back to back against the default limit (120 a minute, bucket of 120): "
        f"**{allowed} allowed, {refused} refused** with a retry-after (the bucket of 120, plus what refilled during the burst).",
        "",
        "Scale-out: run more replicas behind a load balancer with `MCPKIT_REDIS_URL` set, so every replica draws from one bucket "
        "per principal, and move the store to your real database.",
    ]
    text = "\n".join(lines) + "\n"
    print(text)
    if errors:
        print("errors:", errors[:5])
    if a.out:
        Path(a.out).write_text(text)


if __name__ == "__main__":
    main()
