"""`mcpkit`: serve over HTTP or stdio, make development keys and tokens, and export the tool contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import anyio

from mcpkit import __version__, logs
from mcpkit.config import Settings


def cmd_serve(args: argparse.Namespace) -> int:
    s = Settings()
    logs.configure(s.log_level)
    if args.transport == "stdio":
        from mcpkit.auth import Principal
        from mcpkit.server import build
        from mcpkit.store import Store

        local = Principal("local", "stdio", s.stdio_tenant, frozenset(s.stdio_scopes))
        server, _ = build(s, Store(s.database), local=local)
        server.run("stdio")
        return 0
    import uvicorn

    from mcpkit.server import http_app

    uvicorn.run(http_app(s), host=args.host, port=args.port, log_level="warning", proxy_headers=True, server_header=False)
    return 0


def cmd_keys(args: argparse.Namespace) -> int:
    from mcpkit import devkeys

    private, jwks = devkeys.generate(Path(args.dir))
    print(f"wrote {private} and {jwks} (development only)")
    return 0


def cmd_token(args: argparse.Namespace) -> int:
    from mcpkit import devkeys

    s = Settings()
    print(
        devkeys.mint(
            Path(args.key).read_bytes(),
            issuer=s.issuer,
            audience=s.audience,
            subject=args.subject,
            tenant=args.tenant,
            scopes=args.scopes.split(),
            ttl=args.ttl,
        )
    )
    return 0


async def _contract() -> dict[str, object]:
    from mcp import Client

    from mcpkit.auth import Principal
    from mcpkit.server import build
    from mcpkit.store import Store

    server, registry = build(
        Settings(), Store(":memory:"), local=Principal("x", "x", "x", frozenset({"tickets:read", "tickets:write", "tickets:admin"}))
    )
    async with Client(server) as c:
        listed = await c.list_tools()
        templates = await c.list_resource_templates()
        resources = await c.list_resources()
        prompts = await c.list_prompts()
    out = []
    for t in sorted(listed.tools, key=lambda t: t.name):
        d = t.model_dump(by_alias=True, exclude_none=True, mode="json")
        d["x-scopes"] = sorted(registry.tools[t.name].scopes)
        out.append(d)
    return {
        "tools": out,
        "resources": sorted(r.uri for r in resources.resources),
        "resourceTemplates": sorted(r.uri_template for r in templates.resource_templates),
        "prompts": sorted(p.name for p in prompts.prompts),
    }


def cmd_contract(args: argparse.Namespace) -> int:
    text = json.dumps(anyio.run(_contract), indent=2, sort_keys=True) + "\n"
    path = Path(args.file)
    if args.check:
        if not path.exists() or path.read_text() != text:
            print(f"{path} is out of date: the tool contract changed. Run `mcpkit contract` and review the diff.", file=sys.stderr)
            return 1
        print(f"{path} matches the server")
        return 0
    path.write_text(text)
    print(f"wrote {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mcpkit", description=__doc__)
    p.add_argument("--version", action="version", version=f"mcpkit {__version__}")
    sub = p.add_subparsers(required=True)

    s = sub.add_parser("serve", help="run the server")
    s.add_argument("--transport", choices=["http", "stdio"], default="http")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("dev-keys", help="write a development signing key and JWKS (never for production)")
    s.add_argument("--dir", default="dev")
    s.set_defaults(fn=cmd_keys)

    s = sub.add_parser("dev-token", help="mint a development token signed with the development key")
    s.add_argument("--key", default="dev/dev-private.pem")
    s.add_argument("--subject", default="dev-user")
    s.add_argument("--tenant", default="acme")
    s.add_argument("--scopes", default="tickets:read tickets:write")
    s.add_argument("--ttl", type=int, default=900)
    s.set_defaults(fn=cmd_token)

    s = sub.add_parser("contract", help="write the tool contract (schemas, scopes, annotations), or --check it in CI")
    s.add_argument("--file", default="schemas/contract.json")
    s.add_argument("--check", action="store_true")
    s.set_defaults(fn=cmd_contract)

    args = p.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
