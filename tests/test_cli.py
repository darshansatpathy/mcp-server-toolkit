from __future__ import annotations

from pathlib import Path

import pytest


from mcpkit.cli import main

ROOT = Path(__file__).resolve().parents[1]


def test_committed_contract_matches_the_server(capsys):
    assert main(["contract", "--check", "--file", str(ROOT / "schemas" / "contract.json")]) == 0


def test_contract_drift_is_caught(tmp_path):
    p = tmp_path / "c.json"
    p.write_text((ROOT / "schemas" / "contract.json").read_text().replace('"maxLength": 5000', '"maxLength": 9000', 1))
    assert main(["contract", "--check", "--file", str(p)]) == 1


def test_dev_keys_and_token(tmp_path, monkeypatch, capsys):
    import jwt

    assert main(["dev-keys", "--dir", str(tmp_path)]) == 0
    capsys.readouterr()
    monkeypatch.setenv("MCPKIT_AUDIENCE", "http://x/mcp")
    assert main(["dev-token", "--key", str(tmp_path / "dev-private.pem"), "--scopes", "tickets:read", "--tenant", "t1"]) == 0
    claims = jwt.decode(capsys.readouterr().out.strip(), options={"verify_signature": False})
    assert claims["aud"] == "http://x/mcp" and claims["tenant"] == "t1" and claims["scope"] == "tickets:read"
    assert oct((tmp_path / "dev-private.pem").stat().st_mode)[-3:] == "600"


def test_version(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    assert "mcpkit 0.1.0" in capsys.readouterr().out


@pytest.mark.anyio
async def test_stdio_transport_runs_as_the_local_principal(tmp_path):
    import os
    import sys

    from mcp import Client, StdioServerParameters

    env = os.environ | {"MCPKIT_DATABASE": str(tmp_path / "s.db"), "MCPKIT_STDIO_TENANT": "me", "MCPKIT_STDIO_SCOPES": '["tickets:read"]'}
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcpkit.cli", "serve", "--transport", "stdio"], env=env)
    async with Client(params) as c:
        assert {t.name for t in (await c.list_tools()).tools} == {"search_tickets", "get_ticket"}
        assert (await c.call_tool("search_tickets", {})).structured_content == {"items": [], "next_cursor": None}
        assert (await c.call_tool("create_ticket", {"title": "abc", "body": "x"})).is_error
