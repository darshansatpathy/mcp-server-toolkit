"""The deployed shape: uvicorn, Streamable HTTP, real signed tokens."""

from __future__ import annotations

import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

pytestmark = pytest.mark.anyio


def _client(base: str, token: str | None) -> Client:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return Client(streamable_http_client(f"{base}/mcp", http_client=httpx2.AsyncClient(headers=headers, timeout=10)))


async def test_full_flow_over_http(live, token):
    async with _client(live, token("tickets:read tickets:write")) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert names == {"search_tickets", "get_ticket", "create_ticket", "add_comment"}
        r = await c.call_tool("create_ticket", {"title": "Login loop", "body": "SSO redirects forever."})
        tid = r.structured_content["ticket"]["id"]
        assert r.structured_content["ticket"]["created_by"] == "ana"
        got = await c.call_tool("get_ticket", {"ticket_id": tid})
        assert got.structured_content["title"] == "Login loop"


async def test_no_token_is_401_with_resource_metadata(live):
    r = httpx.post(f"{live}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    # The metadata URL is derived from the resource identifier (the token audience), per RFC 9728.
    assert 'resource_metadata="http://127.0.0.1/.well-known/oauth-protected-resource/mcp"' in r.headers["www-authenticate"]
    meta = httpx.get(f"{live}/.well-known/oauth-protected-resource/mcp").json()
    assert meta["authorization_servers"] == ["https://idp.test"]


@pytest.mark.parametrize("bad", [{"audience": "https://other/mcp"}, {"ttl": -300}, {"issuer": "https://evil"}])
async def test_bad_tokens_are_401(live, token, bad):
    r = httpx.post(
        f"{live}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers={"Authorization": f"Bearer {token(**bad)}"}
    )
    assert r.status_code == 401


async def test_missing_scope_is_403_insufficient_scope(live, token):
    body = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "close_ticket", "arguments": {"ticket_id": "T-000001", "resolution": "fixed"}},
    }
    headers = {"Authorization": f"Bearer {token('tickets:read tickets:write')}", "Accept": "application/json, text/event-stream"}
    r = httpx.post(f"{live}/mcp", json=body, headers=headers)
    assert r.status_code == 403
    www = r.headers["www-authenticate"]
    assert 'error="insufficient_scope"' in www and 'scope="tickets:admin"' in www
    assert 'resource_metadata="http://127.0.0.1/.well-known/oauth-protected-resource/mcp"' in www


async def test_dns_rebinding_and_body_limits(live, token):
    headers = {"Authorization": f"Bearer {token()}", "Host": "evil.example", "Accept": "application/json, text/event-stream"}
    r = httpx.post(f"{live}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=headers)
    assert r.status_code in (403, 421)
    big = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "create_ticket", "arguments": {"title": "x", "body": "y" * 300_000}},
    }
    r = httpx.post(f"{live}/mcp", json=big, headers={"Authorization": f"Bearer {token()}"})
    assert r.status_code == 413


async def test_tenant_isolation_over_http(live, token):
    async with _client(live, token(tenant="acme")) as c:
        tid = (await c.call_tool("create_ticket", {"title": "Acme only", "body": "secret"})).structured_content["ticket"]["id"]
    async with _client(live, token(tenant="globex", subject="eve")) as c:
        r = await c.call_tool("get_ticket", {"ticket_id": tid})
        assert r.is_error and "not found" in r.content[0].text


async def test_health_ready_metrics(live, token):
    assert httpx.get(f"{live}/healthz").text == "ok"
    assert httpx.get(f"{live}/readyz").status_code == 200
    async with _client(live, token()) as c:
        await c.call_tool("search_tickets", {})
    text = httpx.get(f"{live}/metrics").text
    assert 'mcp_requests_total{method="tools/call",outcome="ok",tool="search_tickets"}' in text
