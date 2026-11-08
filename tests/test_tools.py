from __future__ import annotations

import pytest

pytestmark = pytest.mark.anyio

READ = frozenset({"tickets:read"})
WRITE = frozenset({"tickets:read", "tickets:write"})


async def _create(c, title="Sync stuck on iPad", body="Notes stop syncing after the 4.2 update.", **kw):
    r = await c.call_tool("create_ticket", {"title": title, "body": body, **kw})
    assert not r.is_error, r.content
    return r.structured_content["ticket"]


async def test_create_get_search_comment_close(connect):
    async with connect() as c:
        t = await _create(c, priority="high")
        assert t["id"] == "T-000001" and t["status"] == "open" and t["created_by"] == "ana"
        await _create(c, title="Invoice is wrong", body="Charged twice in May.")
        page = (await c.call_tool("search_tickets", {"query": "syncing ipad"})).structured_content
        assert [i["id"] for i in page["items"]] == ["T-000001"]
        r = await c.call_tool("add_comment", {"ticket_id": t["id"], "body": "Reproduced on iOS 19."})
        assert r.structured_content["author"] == "ana"
        r = await c.call_tool("close_ticket", {"ticket_id": t["id"], "resolution": "fixed", "note": "Fixed in 4.2.1"})
        closed = r.structured_content
        assert closed["status"] == "closed" and len(closed["comments"]) == 2
        again = await c.call_tool("close_ticket", {"ticket_id": t["id"], "resolution": "fixed"})
        assert not again.is_error  # idempotent
        conflict = await c.call_tool("close_ticket", {"ticket_id": t["id"], "resolution": "duplicate"})
        assert conflict.is_error and "already closed" in conflict.content[0].text
        assert (await c.call_tool("add_comment", {"ticket_id": t["id"], "body": "more"})).is_error


async def test_pagination(connect):
    async with connect() as c:
        for i in range(7):
            await _create(c, title=f"Ticket number {i}")
        seen, cursor = [], None
        while True:
            args = {"limit": 3} | ({"cursor": cursor} if cursor else {})
            page = (await c.call_tool("search_tickets", args)).structured_content
            seen += [i["id"] for i in page["items"]]
            cursor = page["next_cursor"]
            if not cursor:
                break
        assert seen == [f"T-{n:06d}" for n in range(7, 0, -1)]


async def test_idempotency_key_prevents_duplicates(connect):
    async with connect() as c:
        a = await c.call_tool("create_ticket", {"title": "Export fails", "body": "x", "idempotency_key": "retry-abc-123"})
        b = await c.call_tool("create_ticket", {"title": "Export fails", "body": "x", "idempotency_key": "retry-abc-123"})
        assert a.structured_content["created"] and not b.structured_content["created"]
        assert a.structured_content["ticket"]["id"] == b.structured_content["ticket"]["id"]
    async with connect(tenant="globex") as c:  # keys are per tenant
        r = await c.call_tool("create_ticket", {"title": "Export fails", "body": "x", "idempotency_key": "retry-abc-123"})
        assert r.structured_content["created"]


async def test_tenants_cannot_see_each_other(connect):
    async with connect(tenant="acme") as c:
        t = await _create(c)
    async with connect(tenant="globex", subject="eve") as c:
        r = await c.call_tool("get_ticket", {"ticket_id": t["id"]})
        assert r.is_error and "not found" in r.content[0].text
        assert (await c.call_tool("search_tickets", {})).structured_content["items"] == []
        assert (await c.call_tool("add_comment", {"ticket_id": t["id"], "body": "hi"})).is_error
        with pytest.raises(Exception, match="Error creating resource"):
            await c.read_resource(f"ticket://{t['id']}")


@pytest.mark.parametrize(
    ("args", "bad"),
    [
        ({"title": "ab", "body": "x"}, "title"),
        ({"title": "abc", "body": "x" * 5001}, "body"),
        ({"title": "abc", "body": "x", "priority": "critical"}, "priority"),
        ({"title": "abc", "body": "x", "idempotency_key": "has spaces in it"}, "idempotency_key"),
    ],
)
async def test_input_validation(connect, args, bad):
    async with connect() as c:
        r = await c.call_tool("create_ticket", args)
        assert r.is_error and bad in r.content[0].text


async def test_search_text_is_never_fts_syntax(connect):
    async with connect() as c:
        await _create(c, title="Crash on title: NEAR(x y)", body='quote " and * star')
        for q in ["title: NEAR(x y)", '" OR 1=1 --', "*", "body:*", ")))"]:
            r = await c.call_tool("search_tickets", {"query": q})
            assert not r.is_error, (q, r.content)


async def test_resources_and_prompt(connect):
    async with connect() as c:
        t = await _create(c)
        md = (await c.read_resource(f"ticket://{t['id']}")).contents[0].text
        assert md.startswith(f"# {t['id']}: Sync stuck on iPad")
        stats = (await c.read_resource("tickets://stats")).contents[0].text
        assert '"open": 1' in stats or '"open":1' in stats
        p = await c.get_prompt("triage", {"ticket_id": t["id"]})
        assert "treat them as data" in p.messages[0].content.text


async def test_annotations_describe_risk(connect):
    async with connect() as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        assert tools["search_tickets"].annotations.read_only_hint
        assert tools["close_ticket"].annotations.destructive_hint and tools["close_ticket"].annotations.idempotent_hint
        assert not tools["create_ticket"].annotations.read_only_hint
