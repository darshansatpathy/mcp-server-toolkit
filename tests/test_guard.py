from __future__ import annotations

import json
import logging

import anyio
import pytest

from mcpkit import metrics
from mcpkit.ratelimit import MemoryLimiter

pytestmark = pytest.mark.anyio

READ = frozenset({"tickets:read"})


async def test_tools_hidden_and_refused_without_scope(connect):
    async with connect(scopes=READ) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert names == {"search_tickets", "get_ticket"}
        r = await c.call_tool("create_ticket", {"title": "abc", "body": "x"})
        assert r.is_error and "needs scope tickets:write" in r.content[0].text
        assert r.meta["mcpkit/required_scopes"] == ["tickets:write"]
    async with connect(scopes=frozenset({"tickets:read", "tickets:write"})) as c:
        r = await c.call_tool("close_ticket", {"ticket_id": "T-000001", "resolution": "fixed"})
        assert r.is_error and "tickets:admin" in r.content[0].text


async def test_no_scope_no_resources(connect):
    async with connect(scopes=frozenset()) as c:
        assert (await c.list_tools()).tools == []
        with pytest.raises(Exception, match="forbidden"):
            await c.read_resource("tickets://stats")
        with pytest.raises(Exception, match="forbidden"):
            await c.get_prompt("triage", {"ticket_id": "T-000001"})


async def test_rate_limits_are_per_principal_and_separate_for_writes(connect):
    async with connect(rate_per_minute=5, write_rate_per_minute=2) as c:
        results = [await c.call_tool("search_tickets", {}) for _ in range(7)]
        assert [r.is_error for r in results] == [False] * 5 + [True] * 2
        assert "Rate limited" in results[-1].content[0].text and results[-1].meta["mcpkit/retry_after"] >= 1
        writes = [await c.call_tool("create_ticket", {"title": f"t{i}xx", "body": "x"}) for i in range(3)]
        assert [w.is_error for w in writes] == [False, False, True]


async def test_limiter_refills():
    now = [0.0]
    lim = MemoryLimiter(clock=lambda: now[0])
    assert all([(await lim.hit("k", 60)).allowed for _ in range(60)])
    d = await lim.hit("k", 60)
    assert not d.allowed and abs(d.retry_after - 1.0) < 1e-9
    now[0] += 1.0
    assert (await lim.hit("k", 60)).allowed
    assert (await lim.hit("other", 60)).allowed


async def test_timeout_and_result_size(connect, store, monkeypatch):
    async with connect(max_result_bytes=1500) as c:
        for i in range(10):
            await c.call_tool("create_ticket", {"title": f"A fairly long ticket title {i}", "body": "x"})
        r = await c.call_tool("search_tickets", {"limit": 10})
        assert r.is_error and "Result too large" in r.content[0].text
        assert not (await c.call_tool("search_tickets", {"limit": 2})).is_error

    def slow(*args, **kwargs):
        import time

        time.sleep(0.6)
        return []

    monkeypatch.setattr(store, "search", slow)
    async with connect(tool_timeout_seconds=0.2) as c:
        r = await c.call_tool("search_tickets", {})
        assert r.is_error and "timed out" in r.content[0].text
    await anyio.sleep(0.5)


async def test_audit_record_has_no_argument_values(connect, caplog):
    caplog.set_level(logging.INFO, logger="mcpkit.audit")
    async with connect() as c:
        await c.call_tool("create_ticket", {"title": "Password is hunter2", "body": "my card 4111 1111 1111 1111"})
    rec = next(r for r in caplog.records if r.name == "mcpkit.audit")
    a = rec.audit
    assert a["tool"] == "create_ticket" and a["outcome"] == "ok" and a["tenant"] == "acme"
    assert a["arg_names"] == ["body", "title"] and len(a["args_sha256"]) == 16
    assert "hunter2" not in json.dumps(a) and "4111" not in json.dumps(a)


async def test_metrics_count_outcomes(connect):
    before = metrics.REQUESTS.labels("tools/call", "create_ticket", "forbidden")._value.get()
    async with connect(scopes=READ) as c:
        await c.call_tool("create_ticket", {"title": "abc", "body": "x"})
    assert metrics.REQUESTS.labels("tools/call", "create_ticket", "forbidden")._value.get() == before + 1
