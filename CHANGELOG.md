# Changelog

## 0.1.0

- MCP server on the Python MCP SDK 2.x over Streamable HTTP (stateless JSON) and stdio.
- OAuth 2.1 resource-server auth: JWT verification against a JWKS with pinned issuer, audience and asymmetric algorithms; protected-resource metadata; 401 and 403 `insufficient_scope` responses.
- Per-tool scopes; tools hidden from callers without them; tenant isolation from a token claim.
- Rate limits per principal for reads and writes, in memory or shared through Redis; tool timeouts; result and request size limits.
- JSON audit log without argument values; Prometheus metrics; health and readiness endpoints.
- Example ticket domain: search with cursors, idempotent create, comments, idempotent close; a resource template and a prompt.
- Tool contract in `schemas/contract.json`, checked in CI. Container image, Compose stack and Kubernetes manifests. Load test.
