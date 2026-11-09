#!/usr/bin/env python3
"""
Observability MCP Server

Exposes project health, logs, traces, and metrics as MCP tools so agents can
query the observability stack without knowing the underlying APIs.

Connect at: http://<host>:8000/mcp  (Streamable HTTP transport)
"""

import asyncio
import contextlib
import os
import secrets
from datetime import datetime, timezone
from typing import Any

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
LOKI_URL = os.getenv("LOKI_URL", "http://loki:3100")
JAEGER_URL = os.getenv("JAEGER_URL", "http://jaeger:16686")
MCP_API_KEY = os.getenv("MCP_API_KEY", "")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))
MCP_ALLOWED_HOSTS = [h.strip() for h in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]

mcp = FastMCP(
    "Observability",
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(MCP_ALLOWED_HOSTS),
        allowed_hosts=MCP_ALLOWED_HOSTS,
    ),
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _now_ns() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1e9)


async def _loki_instant(logql: str) -> list:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            f"{LOKI_URL}/loki/api/v1/query",
            params={"query": logql, "time": str(_now_ns())},
        )
        r.raise_for_status()
    return r.json().get("data", {}).get("result", [])


async def _loki_range(
    logql: str, range_min: int, limit: int, direction: str = "backward"
) -> list[dict]:
    now = _now_ns()
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            f"{LOKI_URL}/loki/api/v1/query_range",
            params={
                "query": logql,
                "start": str(now - range_min * 60 * int(1e9)),
                "end": str(now),
                "limit": min(limit, 200),
                "direction": direction,
            },
        )
        r.raise_for_status()

    lines = []
    for stream in r.json().get("data", {}).get("result", []):
        labels = stream.get("stream", {})
        service = labels.get("service_name", labels.get("container_name", "unknown"))
        level = labels.get("level", labels.get("severity", ""))
        trace_id = labels.get("trace_id", labels.get("traceID", ""))
        for ts_ns, line in stream.get("values", []):
            ts = datetime.fromtimestamp(int(ts_ns) / 1e9, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            entry: dict[str, str] = {"time": ts, "service": service, "log": line}
            if level:
                entry["level"] = level
            if trace_id:
                entry["trace_id"] = trace_id
            lines.append(entry)

    lines.sort(key=lambda x: x["time"], reverse=True)
    return lines[:limit]


def _build_stream_selector(
    project: str, service: str = "", level: str = ""
) -> str:
    """Build a Loki stream selector from project, optional service, and optional level."""
    matchers = [f'service_namespace="{project}"']
    if service:
        matchers.append(f'service_name="{service}"')
    if level:
        matchers.append(f'level="{level}"')
    return "{" + ", ".join(matchers) + "}"


# An error is a log line whose LEVEL is error/fatal/panic/critical — not any
# line that merely contains the word "error". The old substring match counted
# `"level":"WARN"` lines like osint-map's "news: feed failed ... stream error"
# (a flaky upstream RSS feed) as errors, inflating a healthy app's error rate.
# This targets the level field across the formats these projects actually emit:
#   JSON      "level":"error"
#   logfmt    level=error
#   bracket   [fatal]
#   prefixed  ] ERROR / ] FATAL:  (e.g. "[time] [http] ERROR ...")
# Validated against a week of real logs: catches genuine 500s, unhandled
# rejections, and [fatal] request errors; ignores WARN/DEBUG lines that only
# mention "error" in a message, field, path, or category label.
ERROR_LEVEL_RE = (
    r'(?i)("level"\s*:\s*"(error|fatal|panic|critical)"'
    r"|\blevel\s*=\s*\"?(error|fatal|panic|critical)"
    r"|\[(error|fatal|panic|crit)\]"
    r"|\]\s*(error|fatal|panic|critical)\b)"
)


# ── tools ──────────────────────────────────────────────────────────────────────


@mcp.tool()
async def list_projects() -> list[str]:
    """
    List all projects currently being observed, identified by their namespace label.

    A project appears here once it has joined the observability Docker network and
    sent at least one log. Use the returned names as the `project` argument in
    other tools.
    """
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{LOKI_URL}/loki/api/v1/label/service_namespace/values")
        r.raise_for_status()
    return r.json().get("data", [])


@mcp.tool()
async def list_services(project: str) -> list[str]:
    """
    List all individual service names within a project namespace.

    Use this to discover service names before filtering logs by service.
    The returned names can be passed as the `service` argument in query_logs
    and get_recent_errors.

    Args:
        project: The project namespace (e.g. "trivia-bot")
    """
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            f"{LOKI_URL}/loki/api/v1/label/service_name/values",
            params={"query": f'{{service_namespace="{project}"}}'},
        )
        r.raise_for_status()
    return r.json().get("data", [])


@mcp.tool()
async def get_project_health(project: str) -> dict[str, Any]:
    """
    Get a health summary for a project over the last hour.

    Returns log volume, error count, error rate percentage, and whether
    any distributed traces have been received for this project.

    Args:
        project: The project namespace (e.g. "trivia-bot", "my-api")
    """
    # LogQL backtick strings are raw — no Go escape processing — so the regex's
    # own quotes and backslashes pass through untouched.
    total_result, error_result = await asyncio.gather(
        _loki_instant(f'sum(count_over_time({{service_namespace="{project}"}}[1h]))'),
        _loki_instant(
            f"sum(count_over_time({{service_namespace=\"{project}\"}} |~ `{ERROR_LEVEL_RE}` [1h]))"
        ),
    )

    log_count = int(total_result[0]["value"][1]) if total_result else 0
    error_count = int(error_result[0]["value"][1]) if error_result else 0

    trace_services: list[str] = []
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get(f"{JAEGER_URL}/api/services")
        if r.status_code == 200:
            trace_services = r.json().get("data", [])

    return {
        "project": project,
        "period": "last 1 hour",
        "log_count": log_count,
        "error_count": error_count,
        "error_rate_pct": round(error_count / log_count * 100, 1) if log_count else 0,
        "has_traces": any(project in s for s in trace_services),
    }


@mcp.tool()
async def query_logs(
    project: str,
    search: str = "",
    service: str = "",
    level: str = "",
    regex: str = "",
    range_minutes: int = 60,
    limit: int = 20,
) -> list[dict[str, str]]:
    """
    Query recent logs for a project from Loki.

    Use list_projects() to find valid project names, list_services() to find
    service names within a project.

    Results include time, service, log, and optionally level and trace_id.
    Results are sorted newest-first.

    Args:
        project: The namespace/project name (e.g. "trivia-bot")
        search: Exact substring to filter logs (e.g. "timeout", "payment failed")
        service: Filter to a specific service within the project (e.g. "api", "worker")
        level: Filter by log level label — one of: debug, info, warn, error
        regex: Regular expression to filter logs (e.g. "user_id=\\d+", "(?i)timeout")
        range_minutes: How many minutes back to search (default 60, max 10080 = 7 days)
        limit: Max log lines to return (default 20, max 200)
    """
    logql = _build_stream_selector(project, service, level)
    if search:
        logql += f" |= `{search}`"
    if regex:
        logql += f' |~ "{regex}"'
    return await _loki_range(logql, range_minutes, limit)


@mcp.tool()
async def get_recent_errors(
    project: str,
    service: str = "",
    range_minutes: int = 60,
    limit: int = 20,
) -> list[dict[str, str]]:
    """
    Return the most recent error/fatal/panic log lines for a project.

    Matches by log LEVEL, not by any line mentioning "error" — so a WARN about a
    flaky upstream feed won't show up here. To hunt those, use query_logs with a
    search term.

    Args:
        project: The namespace/project name
        service: Optionally narrow to a specific service within the project
        range_minutes: How many minutes back to search (default 60)
        limit: Max error lines to return (default 20, max 200)
    """
    selector = _build_stream_selector(project, service)
    logql = f"{selector} |~ `{ERROR_LEVEL_RE}`"
    return await _loki_range(logql, range_minutes, limit)


@mcp.tool()
async def query_logs_raw(
    logql: str,
    range_minutes: int = 60,
    limit: int = 20,
) -> list[dict[str, str]]:
    """
    Run an arbitrary LogQL log query against Loki.

    Use this when the higher-level query_logs tool is not expressive enough.
    Results include time, service, log, and optionally level and trace_id.

    Example LogQL queries:
      - '{service_namespace="myapp"} | json | level="error" | line_format "{{.msg}}"'
      - '{service_namespace="myapp", service_name="api"} |= "user_id=42"'
      - '{service_namespace="myapp"} | logfmt | duration > 500ms'
      - 'sum by (service_name) (rate({service_namespace="myapp"}[5m]))'  # metric query — use query_metrics instead

    Args:
        logql: A valid LogQL log selector expression
        range_minutes: How many minutes back to search (default 60)
        limit: Max log lines to return (default 20, max 200)
    """
    return await _loki_range(logql, range_minutes, limit)


@mcp.tool()
async def query_metrics(promql: str) -> dict[str, Any]:
    """
    Run a PromQL instant query against Prometheus (current value).

    Use query_metrics_range() instead if you need a time series.

    Useful examples:
      - 'up'  — which scrape targets are reachable
      - 'rate(http_requests_total{service_namespace="myapp"}[5m])'  — request rate
      - 'sum by (service_namespace) (rate(otelcol_exporter_sent_spans_total[5m]))'  — traces/sec per project
      - 'histogram_quantile(0.99, rate(http_request_duration_seconds_bucket{service_namespace="myapp"}[5m]))'  — p99 latency

    Args:
        promql: A valid PromQL expression

    Returns the Prometheus result (type + result array).
    """
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": promql})
        r.raise_for_status()
    return r.json().get("data", {})


@mcp.tool()
async def query_metrics_range(
    promql: str,
    range_minutes: int = 60,
    step_seconds: int = 60,
) -> dict[str, Any]:
    """
    Run a PromQL range query against Prometheus to get a time series.

    Use this to see how a metric has changed over time (trends, spikes, patterns).
    Use query_metrics() for a single current snapshot instead.

    Useful examples:
      - 'rate(http_requests_total{service_namespace="myapp"}[5m])'  — request rate over time
      - 'sum by (service_name) (rate(http_server_duration_milliseconds_count[5m]))'  — per-service RPS trend
      - 'histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))'  — p95 latency trend

    Args:
        promql: A valid PromQL expression
        range_minutes: How many minutes back (default 60)
        step_seconds: Data point interval in seconds (default 60). Use smaller values for
                      shorter ranges, larger values for longer ranges to keep result size manageable.

    Returns result type and matrix of {metric labels, values: [[timestamp, value], ...]}.
    """
    now = datetime.now(timezone.utc).timestamp()
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            f"{PROMETHEUS_URL}/api/v1/query_range",
            params={
                "query": promql,
                "start": now - range_minutes * 60,
                "end": now,
                "step": step_seconds,
            },
        )
        r.raise_for_status()
    return r.json().get("data", {})


@mcp.tool()
async def list_trace_services() -> list[str]:
    """
    List all service names that have sent distributed traces to Jaeger.

    Use the returned names as the `service` argument in get_traces().
    Note: Jaeger service names match the obs.service.name Docker label,
    which may differ from the Loki service_name label.
    """
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{JAEGER_URL}/api/services")
        r.raise_for_status()
    services = r.json().get("data", [])
    return [s for s in services if s != "jaeger-all-in-one"]


@mcp.tool()
async def get_traces(
    service: str,
    operation: str = "",
    limit: int = 10,
    min_duration_ms: int = 0,
) -> list[dict[str, Any]]:
    """
    Get recent distributed traces for a service from Jaeger.

    Use list_trace_services() first to discover valid service names.

    Args:
        service: The Jaeger service name (matches the obs.service.name Docker label).
                 Use list_trace_services() to find valid names.
        operation: Optionally filter to a specific operation/endpoint (e.g. "GET /api/users")
        limit: Number of traces to return (default 10, max 100)
        min_duration_ms: Only return traces slower than this many milliseconds (default 0 = all)
    """
    params: dict[str, Any] = {"service": service, "limit": min(limit, 100)}
    if operation:
        params["operation"] = operation
    if min_duration_ms:
        params["minDuration"] = f"{min_duration_ms}ms"

    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{JAEGER_URL}/api/traces", params=params)
        if r.status_code == 404:
            return []
        r.raise_for_status()

    out = []
    for trace in r.json().get("data", []):
        spans = trace.get("spans", [])
        root = next(
            (s for s in spans if not s.get("references")),
            spans[0] if spans else None,
        )
        if root:
            error_spans = [
                s for s in spans
                if any(t.get("key") == "error" for t in s.get("tags", []))
            ]
            out.append(
                {
                    "traceId": trace.get("traceID"),
                    "operation": root.get("operationName"),
                    "duration_ms": round(root.get("duration", 0) / 1000, 2),
                    "span_count": len(spans),
                    "error_span_count": len(error_spans),
                    "start_time": datetime.fromtimestamp(
                        root.get("startTime", 0) / 1e6, tz=timezone.utc
                    ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "has_errors": bool(error_spans),
                    "error_operations": [s.get("operationName") for s in error_spans],
                }
            )
    return out


@mcp.tool()
async def get_container_health(namespace: str = "") -> list[dict[str, Any]]:
    """
    Return the HTTP health probe status for all observed containers.

    Uses blackbox exporter probe results from Prometheus. Each entry shows
    whether the container's health endpoint is up, its HTTP status code,
    response time, and the endpoint URL.

    Args:
        namespace: Optional namespace to filter by (e.g. "gratitude"). Returns all if omitted.
    """
    label = f'namespace="{namespace}"' if namespace else ""

    async with httpx.AsyncClient(timeout=10) as c:
        success_r, duration_r, status_r = await asyncio.gather(
            c.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": f"probe_success{{{label}}}"}),
            c.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": f"probe_duration_seconds{{{label}}}"}),
            c.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": f"probe_http_status_code{{{label}}}"}),
        )

    def index(r) -> dict[str, float]:
        return {
            m["metric"].get("instance", ""): float(m["value"][1])
            for m in r.json().get("data", {}).get("result", [])
        }

    duration = index(duration_r)
    http_status = index(status_r)

    results = []
    for m in success_r.json().get("data", {}).get("result", []):
        instance = m["metric"].get("instance", "")
        results.append({
            "service": m["metric"].get("job", ""),
            "namespace": m["metric"].get("namespace", ""),
            "endpoint": instance,
            "up": bool(int(m["value"][1])),
            "http_status": int(http_status.get(instance, 0)) or None,
            "response_ms": round(duration.get(instance, 0) * 1000, 2),
        })

    results.sort(key=lambda x: (x["namespace"], x["service"]))
    return results


@mcp.tool()
async def get_stack_health() -> dict[str, str]:
    """
    Check the health of all observability stack components.

    Returns a dict mapping component name to "healthy", "degraded (HTTP NNN)",
    or "unreachable (ErrorType)".
    """
    urls = [
        ("prometheus", f"{PROMETHEUS_URL}/-/ready"),
        ("loki", f"{LOKI_URL}/ready"),
        ("jaeger", f"{JAEGER_URL}/"),
        ("otel_collector", "http://otel-collector:13133/"),
    ]
    checks: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=5) as c:
        for name, url in urls:
            try:
                r = await c.get(url)
                checks[name] = (
                    "healthy" if r.status_code < 400 else f"degraded (HTTP {r.status_code})"
                )
            except Exception as e:
                checks[name] = f"unreachable ({type(e).__name__})"
    return checks


# ── public status endpoint ─────────────────────────────────────────────────────
#
# Unauthenticated, read-only, CORS-open. It backs the public status panel on
# app.example.com and is the ONLY thing on this server a stranger can
# read.
#
# It is an allowlist, not a filter, and it fails closed: with PUBLIC_PROJECTS
# unset it publishes nothing at all. Everything else this stack observes stays
# invisible, so a project joining the network can never start publishing its
# traffic by accident.
#
#   PUBLIC_PROJECTS="trivia-bot:Trivialism,gratitude:Gratitude"
#
# The namespace never leaves the server — only the display label does. Adding a
# project is an env change and a restart, not a code change.

PUBLIC_PROJECTS_RAW = os.getenv("PUBLIC_PROJECTS", "")
PUBLIC_CACHE_TTL = int(os.getenv("PUBLIC_CACHE_TTL", "60"))


def _public_projects() -> list[tuple[str, str]]:
    """Parse PUBLIC_PROJECTS into [(namespace, display_label)]."""
    out: list[tuple[str, str]] = []
    for item in PUBLIC_PROJECTS_RAW.split(","):
        item = item.strip()
        if not item:
            continue
        namespace, _, label = item.partition(":")
        namespace = namespace.strip()
        if not namespace:
            continue
        out.append((namespace, label.strip() or namespace))
    return out


_public_cache: dict[str, Any] = {"at": -1e9, "body": None}


async def _public_health(request: Request) -> JSONResponse:
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Cache-Control": f"public, max-age={PUBLIC_CACHE_TTL}",
    }
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=headers)

    # Cache so a public page can't turn into a load generator against Loki.
    now = asyncio.get_event_loop().time()
    if _public_cache["body"] is not None and now - _public_cache["at"] < PUBLIC_CACHE_TTL:
        return JSONResponse(_public_cache["body"], headers=headers)

    allowed = _public_projects()
    stack = await get_stack_health()

    projects = []
    for namespace, label in allowed:
        try:
            h = await get_project_health(namespace)
            projects.append(
                {
                    "name": label,
                    "log_count": h["log_count"],
                    "error_count": h["error_count"],
                    "error_rate_pct": h["error_rate_pct"],
                }
            )
        except Exception:
            # One bad project must not take the whole panel down.
            projects.append({"name": label, "unavailable": True})

    body = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": "last 1 hour",
        "stack": stack,
        "projects": projects,
    }
    _public_cache["at"] = now
    _public_cache["body"] = body
    return JSONResponse(body, headers=headers)


# ── ASGI app with optional bearer-token auth ───────────────────────────────────

# Paths a stranger may read. Everything else needs the bearer token.
PUBLIC_PATHS = ("/health", "/public/health")


@contextlib.asynccontextmanager
async def _lifespan(app):
    async with mcp.session_manager.run():
        yield


async def _health(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


app = Starlette(
    routes=[
        Route("/health", _health),
        Route("/public/health", _public_health, methods=["GET", "OPTIONS"]),
        Mount("/", app=mcp.streamable_http_app()),
    ],
    lifespan=_lifespan,
)

class _BearerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.method == "OPTIONS" or request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        token = (
            auth.removeprefix("Bearer ").strip()
            if auth.startswith("Bearer ")
            else request.headers.get("X-API-Key", "")
        )
        if not MCP_API_KEY or not secrets.compare_digest(token, MCP_API_KEY):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)

app.add_middleware(_BearerMiddleware)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=MCP_PORT)
