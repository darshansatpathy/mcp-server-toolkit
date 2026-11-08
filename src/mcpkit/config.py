"""Settings, read from MCPKIT_* environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MCPKIT_", env_file=".env", extra="ignore")

    # Identity: this server is an OAuth 2.1 resource server. Tokens come from your IdP.
    issuer: str = "http://127.0.0.1:8000/dev-issuer"
    audience: str = "http://127.0.0.1:8000/mcp"
    jwks_url: str | None = None
    jwks_file: Path | None = Path("dev/jwks.json")
    algorithms: list[str] = Field(default_factory=lambda: ["RS256", "ES256"])
    leeway_seconds: int = 30
    tenant_claim: str = "tenant"

    # Who may call it from a browser (DNS-rebinding protection checks Host and Origin).
    allowed_hosts: list[str] = Field(default_factory=lambda: ["127.0.0.1:*", "localhost:*"])
    allowed_origins: list[str] = Field(default_factory=list)
    stateless: bool = True

    # Limits.
    rate_per_minute: int = 120
    write_rate_per_minute: int = 20
    tool_timeout_seconds: float = 10.0
    max_result_bytes: int = 64_000
    max_request_bytes: int = 256_000

    # Storage and backends.
    database: Path = Path("tickets.db")
    redis_url: str | None = None

    # Local stdio use runs as this principal (no network, so no token).
    stdio_tenant: str = "local"
    stdio_scopes: list[str] = Field(default_factory=lambda: ["tickets:read"])

    log_level: str = "INFO"
