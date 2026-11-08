"""Prometheus metrics. Served at /metrics."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry()

REQUESTS = Counter("mcp_requests_total", "MCP requests by method, tool and outcome", ["method", "tool", "outcome"], registry=REGISTRY)
LATENCY = Histogram(
    "mcp_request_seconds",
    "MCP request latency",
    ["method", "tool"],
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
    registry=REGISTRY,
)
INFLIGHT = Gauge("mcp_inflight_requests", "MCP requests being handled", registry=REGISTRY)
RATE_LIMITED = Counter("mcp_rate_limited_total", "Tool calls refused by the rate limiter", ["tool"], registry=REGISTRY)
FORBIDDEN = Counter("mcp_forbidden_total", "Requests refused for missing scope", ["method", "tool"], registry=REGISTRY)
AUTH_FAILURES = Counter("mcp_auth_failures_total", "Bearer tokens refused, by reason", ["reason"], registry=REGISTRY)
