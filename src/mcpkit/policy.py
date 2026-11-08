"""Per-tool policy and the guard that enforces it on every request.

Each tool declares the scopes it needs and whether it writes. The guard, an MCP server middleware,
runs around every request:

1. Resolves the principal (the verified bearer token over HTTP; a configured local principal over stdio).
2. Hides tools the caller cannot use from `tools/list`, and refuses calls to them.
3. Applies the read or write rate limit for that principal.
4. Runs the tool under a timeout and refuses results larger than the limit.
5. Writes an audit record and metrics for the call. Arguments are never logged, only their names and a hash.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

import anyio
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_REQUEST
from pydantic import BaseModel

from mcpkit import metrics
from mcpkit.auth import Principal
from mcpkit.ratelimit import Limiter, retry_header

audit_log = logging.getLogger("mcpkit.audit")
current: contextvars.ContextVar[Principal | None] = contextvars.ContextVar("mcpkit_principal", default=None)

F = TypeVar("F", bound=Callable[..., Any])

# Methods other than tools that need a scope. Discovery (initialize, tools/list, ping) needs only a valid token.
METHOD_SCOPES = {
    "resources/read": "tickets:read",
    "resources/list": "tickets:read",
    "resources/templates/list": "tickets:read",
    "prompts/get": "tickets:read",
}


@dataclass(frozen=True)
class ToolPolicy:
    scopes: frozenset[str]
    write: bool = False


@dataclass
class Registry:
    tools: dict[str, ToolPolicy] = field(default_factory=dict)

    def missing(self, tool: str, scopes: frozenset[str]) -> frozenset[str]:
        p = self.tools.get(tool)
        return frozenset() if p is None else p.scopes - scopes


def principal() -> Principal:
    p = current.get()
    if p is None:
        raise MCPError(INVALID_REQUEST, "no authenticated principal")
    return p


def tool_error(text: str, **meta: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": True, "resultType": "complete"}
    if meta:
        out["_meta"] = {f"mcpkit/{k}": v for k, v in meta.items()}
    return out


def _dump(result: HandlerResult) -> dict[str, Any]:
    if isinstance(result, BaseModel):
        return result.model_dump(by_alias=True, exclude_none=True, mode="json")
    return result or {}


class Guard:
    def __init__(
        self,
        registry: Registry,
        limiter: Limiter,
        *,
        read_per_minute: int,
        write_per_minute: int,
        timeout: float,
        max_result_bytes: int,
        local: Principal | None = None,
        tenant_claim: str = "tenant",
    ):
        self.registry, self.limiter = registry, limiter
        self.read_per_minute, self.write_per_minute = read_per_minute, write_per_minute
        self.timeout, self.max_result_bytes = timeout, max_result_bytes
        self.local, self.tenant_claim = local, tenant_claim

    def _principal(self) -> Principal | None:
        token = get_access_token()
        if token is not None:
            return Principal.from_token(token, self.tenant_claim)
        return self.local

    async def __call__(self, ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
        if ctx.request_id is None:  # notifications
            return await call_next(ctx)
        who = self._principal()
        reset = current.set(who)
        try:
            if ctx.method == "tools/list":
                return self._filter(await call_next(ctx), who)
            if ctx.method == "tools/call":
                return await self._call_tool(ctx, call_next, who)
            need = METHOD_SCOPES.get(ctx.method)
            if need and (who is None or need not in who.scopes):
                metrics.FORBIDDEN.labels(ctx.method, "").inc()
                raise MCPError(INVALID_REQUEST, f"forbidden: {ctx.method} needs scope {need}")
            return await call_next(ctx)
        finally:
            current.reset(reset)

    def _filter(self, result: HandlerResult, who: Principal | None) -> HandlerResult:
        scopes = who.scopes if who else frozenset()
        data = _dump(result)
        data["tools"] = [t for t in data.get("tools", []) if not self.registry.missing(t["name"], scopes)]
        return data

    async def _call_tool(self, ctx: ServerRequestContext[Any, Any], call_next: CallNext, who: Principal | None) -> HandlerResult:
        params = ctx.params or {}
        name = str(params.get("name", ""))
        args = params.get("arguments") or {}
        started = time.perf_counter()
        outcome, detail = "ok", ""
        metrics.INFLIGHT.inc()
        try:
            if who is None:
                outcome = "unauthenticated"
                return tool_error("Not authenticated.")
            missing = self.registry.missing(name, who.scopes)
            if missing:
                outcome, detail = "forbidden", " ".join(sorted(missing))
                metrics.FORBIDDEN.labels("tools/call", name).inc()
                return tool_error(f"Forbidden: {name} needs scope {detail}.", required_scopes=sorted(missing))
            policy = self.registry.tools.get(name)
            write = bool(policy and policy.write)
            limit = self.write_per_minute if write else self.read_per_minute
            d = await self.limiter.hit(f"{who.tenant}:{who.subject}:{'write' if write else 'read'}", limit)
            if not d.allowed:
                outcome = "rate_limited"
                metrics.RATE_LIMITED.labels(name).inc()
                return tool_error(f"Rate limited: retry after {retry_header(d)} s.", retry_after=float(retry_header(d)))
            try:
                with anyio.fail_after(self.timeout):
                    result = await call_next(ctx)
            except TimeoutError:
                outcome = "timeout"
                return tool_error(f"{name} timed out after {self.timeout:g} s.")
            data = _dump(result)
            size = len(json.dumps(data, separators=(",", ":")).encode())
            if size > self.max_result_bytes:
                outcome, detail = "too_large", str(size)
                return tool_error(f"Result too large ({size} bytes, limit {self.max_result_bytes}); narrow the request.")
            if data.get("isError"):
                outcome = "tool_error"
            return data
        except MCPError:
            outcome = "protocol_error"
            raise
        finally:
            elapsed = time.perf_counter() - started
            metrics.INFLIGHT.dec()
            metrics.REQUESTS.labels("tools/call", name, outcome).inc()
            metrics.LATENCY.labels("tools/call", name).observe(elapsed)
            audit_log.info(
                "tool call",
                extra={
                    "audit": {
                        "tool": name,
                        "outcome": outcome,
                        "detail": detail,
                        "subject": who.subject if who else None,
                        "client_id": who.client_id if who else None,
                        "tenant": who.tenant if who else None,
                        "arg_names": sorted(args) if isinstance(args, dict) else [],
                        "args_sha256": hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()[:16],
                        "ms": round(elapsed * 1000, 2),
                    }
                },
            )
