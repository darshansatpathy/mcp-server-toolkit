# 2. Scopes per tool, enforced twice, and invisible tools for callers without them

Status: accepted

## Context

A model chooses which tools to call. A tool it cannot see, it cannot be talked into calling. A tool it can see but not use produces failed calls and confusing retries.

## Decision

- Every tool declares its scopes and whether it writes, next to its definition (`tickets:read`; `tickets:write`; `tickets:write tickets:admin` for the destructive one).
- `tools/list` returns only the tools the caller's token allows.
- Over HTTP, a call to a tool without its scope gets HTTP 403 with `WWW-Authenticate: Bearer error="insufficient_scope", scope="..."`, as the MCP authorization spec describes, so a client can step up and retry.
- Inside the server, the guard middleware enforces the same rule for every transport, including stdio and in-process clients, and returns a tool error naming the missing scope.
- Tool annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`) are set from the same declaration, so clients can ask for confirmation before destructive calls.

## Consequences

Two enforcement points must agree; both read one registry, and tests cover each.
