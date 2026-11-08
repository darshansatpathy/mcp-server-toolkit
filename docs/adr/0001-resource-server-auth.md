# 1. The server is an OAuth 2.1 resource server, never an authorization server

Status: accepted

## Context

An MCP server exposed over HTTP acts on a user's behalf, so it must know who is calling and what they may do. The MCP authorization specification lets a server be both the authorization server and the resource server, or only the latter. Running an authorization server means storing client registrations, consents and refresh tokens: a second product.

## Decision

- The server only verifies bearer tokens issued by the organisation's identity provider. It advertises the IdP in OAuth protected-resource metadata (RFC 9728) and answers unauthenticated requests with 401 and a `resource_metadata` pointer, so MCP clients discover where to sign in.
- Tokens are JWTs verified against the IdP's JWKS (cached, refetched on an unknown key id for rotation). The verifier pins the issuer, the audience (this server's URL, RFC 8707, so a token minted for another service is refused) and an allowlist of asymmetric algorithms. `alg: none`, HMAC with the public key, expired tokens and tokens without a tenant are refused and counted by reason.
- The tenant comes from a token claim, never from a tool argument.
- Local development uses a generated key pair and `mcpkit dev-token`; the private key is never committed and production holds none.

## Consequences

The server needs an IdP that issues JWT access tokens with an audience. Opaque tokens would need an introspection verifier, which fits the same `TokenVerifier` interface.
