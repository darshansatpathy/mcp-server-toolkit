# 3. Rate limits, timeouts, result limits and an audit log that holds no data

Status: accepted

## Context

Agents call tools in loops. A looping agent can exhaust a backend, a slow query can hold a worker, and a huge result fills the model's context and the bill. Every call must be attributable, but arguments contain customer data.

## Decision

- Token buckets per tenant and subject, separate for reads and writes (120 and 20 a minute by default). One replica uses memory; several share Redis through an atomic Lua script. Refusals are tool errors with a retry-after, so the model can wait rather than fail.
- Every tool runs under a timeout (10 s); store work runs in a worker thread the caller stops waiting for at the deadline. Writes are single transactions, so an abandoned one never leaves half a write.
- Results above a size limit (64 KB) are refused with a message telling the model to narrow the request. Lists are paginated with opaque cursors.
- Request bodies above a limit get 413 before parsing.
- Every call writes one JSON audit line: tool, outcome, subject, client, tenant, duration, argument names and a hash of the arguments. Never the values. Metrics count requests by tool and outcome, refusals by reason, and latency.

## Consequences

The audit hash lets an investigator confirm that a known payload was sent without the log ever holding it.
