# Contributing

```bash
python -m venv .venv && . .venv/bin/activate
make install
make dev-keys     # a local signing key; never commit dev/
make check        # ruff, mypy, contract check, tests
```

Tests start real servers: uvicorn over HTTP with signed tokens, the stdio transport as a subprocess, and Redis when `redis-server` is installed or `REDIS_URL` is set.

## Adding a tool

1. Define it in `src/mcpkit/tools.py` with the `tool(name, scopes, write=...)` helper, typed and constrained arguments (`Annotated[..., Field(...)]`) and a pydantic return model.
2. Take the tenant and subject from `principal()`, never from arguments, and scope every query by tenant in the store.
3. Set annotations honestly: `destructive_hint` for anything that cannot be undone, `idempotent_hint` only if a retry is safe.
4. Raise `ToolError` with a message the model can act on for expected failures.
5. Test it, including the refusal without its scope and the call from another tenant.
6. Run `mcpkit contract` and review the diff in `schemas/contract.json`.

## Commits

Small, focused commits with messages in the imperative. Update `CHANGELOG.md` for anything a user would notice.
