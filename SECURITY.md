# Security policy

Report a vulnerability privately through [GitHub security advisories](https://github.com/darshansatpathy/mcp-server-toolkit/security/advisories/new). Please do not open a public issue. You can expect a first reply within five working days.

## In scope

- Any way to call a tool, read a resource or get a prompt without a valid token over HTTP, or without the required scope over any transport.
- Any way to read or change another tenant's data.
- Token verification: accepting a token with the wrong issuer, audience, algorithm or key, or an expired one.
- Rate, size and time limits that can be bypassed.
- Argument values reaching the audit log or metrics.

## Hardening already in place

Asymmetric algorithms only; pinned issuer and audience; DNS-rebinding protection on Host and Origin; request body limit; non-root container with a read-only root filesystem; restricted Pod Security, dropped capabilities and a default-deny NetworkPolicy in `deploy/k8s`; CodeQL, pip-audit and Dependabot in CI.

## Supported versions

The latest release receives fixes.
