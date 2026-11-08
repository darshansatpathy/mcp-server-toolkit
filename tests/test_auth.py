from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import jwt
import pytest

from mcpkit.auth import JWTVerifier, Principal, StaticKeys

pytestmark = pytest.mark.anyio


@pytest.fixture
def verifier(settings, keys):
    return JWTVerifier(StaticKeys.from_file(keys[1]), settings.issuer, settings.audience, ["RS256"])


async def test_valid_token(verifier, token):
    t = await verifier.verify_token(token("tickets:read"))
    assert t is not None and t.scopes == ["tickets:read"] and t.subject == "ana"
    p = Principal.from_token(t)
    assert p.tenant == "acme" and p.scopes == frozenset({"tickets:read"})


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"audience": "https://other-service/mcp"}, "audience"),
        ({"issuer": "https://evil.test"}, "issuer"),
        ({"ttl": -120}, "expired"),
    ],
)
async def test_refused_claims(verifier, token, change, reason):
    assert await verifier.verify_token(token(**change)) is None
    assert verifier.failures == {reason: 1}


async def test_alg_none_and_hs256_with_the_public_key_are_refused(verifier, settings, keys):
    now = int(time.time())
    claims = {"iss": settings.issuer, "aud": settings.audience, "sub": "x", "tenant": "acme", "iat": now, "exp": now + 60}
    unsigned = jwt.encode(claims, None, algorithm="none", headers={"kid": "dev-1"})
    assert await verifier.verify_token(unsigned) is None

    # The classic confusion attack: HMAC-sign with the public key as the secret. Built by hand,
    # because PyJWT itself refuses to sign with a JWK as an HMAC secret.
    def b64(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    head = b64(json.dumps({"alg": "HS256", "kid": "dev-1", "typ": "JWT"}).encode()) + "." + b64(json.dumps(claims).encode())
    forged = head + "." + b64(hmac.new(keys[1].read_bytes(), head.encode(), hashlib.sha256).digest())
    assert await verifier.verify_token(forged) is None
    assert verifier.failures["algorithm"] == 2


async def test_unknown_key_missing_tenant_and_garbage(verifier, token, keys, settings):
    from mcpkit import devkeys

    assert (
        await verifier.verify_token(
            devkeys.mint(
                keys[0], issuer=settings.issuer, audience=settings.audience, subject="a", tenant="t", scopes=[], kid="rotated-away"
            )
        )
        is None
    )
    assert await verifier.verify_token(token(tenant="")) is None
    assert await verifier.verify_token("not-a-jwt") is None
    assert verifier.failures == {"key": 1, "no_tenant": 1, "invalid": 1}


def test_symmetric_algorithms_cannot_be_configured(keys):
    with pytest.raises(ValueError, match="asymmetric"):
        JWTVerifier(StaticKeys.from_file(keys[1]), "i", "a", ["RS256", "HS256"])
