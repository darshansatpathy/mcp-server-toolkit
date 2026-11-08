from __future__ import annotations

import contextlib
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from mcp import Client

from mcpkit import devkeys
from mcpkit.auth import JWTVerifier, Principal, StaticKeys
from mcpkit.config import Settings
from mcpkit.ratelimit import MemoryLimiter
from mcpkit.server import build, http_app
from mcpkit.store import Store

ALL = frozenset({"tickets:read", "tickets:write", "tickets:admin"})


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def keys(tmp_path_factory: pytest.TempPathFactory) -> tuple[bytes, Path]:
    d = tmp_path_factory.mktemp("keys")
    private, jwks = devkeys.generate(d)
    return private.read_bytes(), jwks


@pytest.fixture
def settings(tmp_path: Path, keys: tuple[bytes, Path]) -> Settings:
    return Settings(
        jwks_file=keys[1],
        database=tmp_path / "t.db",
        issuer="https://idp.test",
        audience="http://127.0.0.1/mcp",
        rate_per_minute=1000,
        write_rate_per_minute=1000,
    )


@pytest.fixture
def store(settings: Settings) -> Iterator[Store]:
    s = Store(settings.database)
    yield s
    s.close()


@pytest.fixture
def connect(settings: Settings, store: Store):
    """In-process client acting as a principal; the server shares one store across principals."""

    @contextlib.asynccontextmanager
    async def make(tenant: str = "acme", scopes: frozenset[str] = ALL, subject: str = "ana", **overrides: Any) -> AsyncIterator[Client]:
        s = settings.model_copy(update=overrides)
        server, _ = build(s, store, limiter=MemoryLimiter(), local=Principal(subject, "test", tenant, scopes))
        async with Client(server) as c:
            yield c

    return make


@pytest.fixture
def token(settings: Settings, keys: tuple[bytes, Path]):
    def make(scopes: str = "tickets:read tickets:write", tenant: str = "acme", subject: str = "ana", **kw: Any) -> str:
        return devkeys.mint(
            keys[0],
            issuer=kw.pop("issuer", settings.issuer),
            audience=kw.pop("audience", settings.audience),
            subject=subject,
            tenant=tenant,
            scopes=scopes.split(),
            **kw,
        )

    return make


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def live(settings: Settings, store: Store) -> Iterator[str]:
    """The HTTP app under uvicorn on a free port, as deployed."""
    port = _free_port()
    s = settings
    verifier = JWTVerifier(StaticKeys.from_file(Path(s.jwks_file or "")), s.issuer, s.audience, s.algorithms)
    app = http_app(s, Store(s.database), verifier=verifier)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(5)
