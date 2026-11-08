"""A development key pair and token minting, so the server can be run and tested without an IdP.

Never used in production: production reads the IdP's JWKS (MCPKIT_JWKS_URL) and holds no private key.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def generate(directory: Path, kid: str = "dev-1") -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = directory / "dev-private.pem"
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    private.chmod(0o600)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public.update({"kid": kid, "use": "sig", "alg": "RS256"})
    jwks = directory / "jwks.json"
    jwks.write_text(json.dumps({"keys": [public]}, indent=2))
    return private, jwks


def mint(
    private_pem: bytes,
    *,
    issuer: str,
    audience: str,
    subject: str,
    tenant: str,
    scopes: list[str],
    ttl: int = 900,
    kid: str = "dev-1",
    extra: dict[str, Any] | None = None,
) -> str:
    t = int(time.time())
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "tenant": tenant,
        "scope": " ".join(scopes),
        "client_id": "dev-client",
        "iat": t,
        "nbf": t,
        "exp": t + ttl,
        "jti": uuid.uuid4().hex,
    } | (extra or {})
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": kid})
