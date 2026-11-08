"""Bearer-token verification for an OAuth 2.1 resource server.

Tokens are JWTs signed by the authorization server. The verifier pins the issuer, the audience
(this server's URL, RFC 8707) and an asymmetric algorithm allowlist, so an `alg: none` token, an
HS256 token signed with the public key, a token for another service, or an expired token is refused.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jwt
from mcp.server.auth.provider import AccessToken

log = logging.getLogger("mcpkit.auth")

ASYMMETRIC = {"RS256", "RS384", "RS512", "ES256", "ES384", "PS256"}


@dataclass
class Principal:
    """Who is calling: the token's subject and client, their tenant, and their scopes."""

    subject: str
    client_id: str
    tenant: str
    scopes: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_token(cls, token: AccessToken, tenant_claim: str = "tenant") -> Principal:
        claims = token.claims or {}
        return cls(token.subject or token.client_id, token.client_id, str(claims.get(tenant_claim, "")), frozenset(token.scopes))


class StaticKeys:
    """Signing keys from a JWKS document on disk (development, tests, air-gapped installs)."""

    def __init__(self, jwks: dict[str, Any]):
        self.keys = {k["kid"]: jwt.PyJWK(k) for k in jwks["keys"]}

    @classmethod
    def from_file(cls, path: Path) -> StaticKeys:
        return cls(json.loads(path.read_text()))

    def get(self, kid: str) -> jwt.PyJWK:
        if kid not in self.keys:
            raise jwt.InvalidKeyError(f"unknown key id {kid!r}")
        return self.keys[kid]


class RemoteKeys:
    """Signing keys from the issuer's JWKS URL, cached, refetched on an unknown key id (rotation)."""

    def __init__(self, url: str, lifespan: int = 300):
        self.client = jwt.PyJWKClient(url, cache_keys=True, lifespan=lifespan, timeout=5)

    def get(self, kid: str) -> jwt.PyJWK:
        return self.client.get_signing_key(kid)


class JWTVerifier:
    """Implements the MCP SDK's `TokenVerifier` protocol."""

    def __init__(
        self,
        keys: StaticKeys | RemoteKeys,
        issuer: str,
        audience: str,
        algorithms: list[str],
        leeway: int = 30,
        tenant_claim: str = "tenant",
    ):
        bad = set(algorithms) - ASYMMETRIC
        if bad:
            raise ValueError(f"only asymmetric algorithms are allowed, not {sorted(bad)}")
        self.keys, self.issuer, self.audience, self.algorithms = keys, issuer, audience, algorithms
        self.leeway, self.tenant_claim = leeway, tenant_claim
        self.failures: dict[str, int] = {}

    def _fail(self, reason: str) -> None:
        self.failures[reason] = self.failures.get(reason, 0) + 1
        from mcpkit import metrics

        metrics.AUTH_FAILURES.labels(reason).inc()
        log.info("token refused", extra={"reason": reason})

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            header = jwt.get_unverified_header(token)
            alg, kid = header.get("alg"), header.get("kid")
            if alg not in self.algorithms:
                self._fail("algorithm")
                return None
            if not kid:
                self._fail("no_key_id")
                return None
            key = self.keys.get(kid)
            claims = jwt.decode(
                token,
                key,
                algorithms=self.algorithms,
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError:
            self._fail("expired")
            return None
        except jwt.InvalidAudienceError:
            self._fail("audience")
            return None
        except jwt.InvalidIssuerError:
            self._fail("issuer")
            return None
        except (jwt.InvalidKeyError, jwt.PyJWKClientError):
            self._fail("key")
            return None
        except jwt.PyJWTError:
            self._fail("invalid")
            return None
        if not claims.get(self.tenant_claim):
            self._fail("no_tenant")
            return None
        raw = claims.get("scope", claims.get("scp", ""))
        scopes = raw.split() if isinstance(raw, str) else list(raw)
        return AccessToken(
            token=token,
            client_id=str(claims.get("client_id", claims.get("azp", claims["sub"]))),
            scopes=scopes,
            expires_at=int(claims["exp"]),
            resource=self.audience,
            subject=str(claims["sub"]),
            claims={"iss": claims["iss"], self.tenant_claim: claims[self.tenant_claim]},
        )


def verifier_from_settings(s: Any) -> JWTVerifier:
    if s.jwks_url:
        keys: StaticKeys | RemoteKeys = RemoteKeys(s.jwks_url)
    elif s.jwks_file:
        keys = StaticKeys.from_file(Path(s.jwks_file))
    else:
        raise ValueError("set MCPKIT_JWKS_URL (your IdP) or MCPKIT_JWKS_FILE")
    return JWTVerifier(keys, s.issuer, s.audience, s.algorithms, s.leeway_seconds, s.tenant_claim)


def now() -> int:
    return int(time.time())
