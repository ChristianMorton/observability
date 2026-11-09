# Observability Stack — Agent Reference

Structured reference for automated agents and tooling. No prose.

---

## Endpoints

| Service | Internal (Docker network) | Host |
|---------|--------------------------|------|
| OTel Collector — OTLP gRPC | `otel-collector:4317` | `localhost:4317` |
| OTel Collector — OTLP HTTP | `otel-collector:4318` | `localhost:4318` |
| OTel Collector — Prometheus exporter | `otel-collector:8889` | `localhost:8889` |
| Loki — push/query | `loki:3100` | `localhost:3100` |
| Jaeger — UI | `jaeger:16686` | `localhost:16686` |
| Jaeger — OTLP gRPC (internal) | `jaeger:4317` | `localhost:14317` |
| Jaeger — OTLP HTTP (internal) | `jaeger:4318` | `localhost:14318` |
| Prometheus | `prometheus:9090` | `localhost:9090` |
| Grafana | `grafana:3003` | `localhost:3003` |
| MCP Server | `mcp-server:8000` | `localhost:8000` |

---

## Label Schema

Labels are set on Docker containers via `docker-compose.yml` labels.

| Label | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `obs.service.name` | string | yes | — | Maps to `service.name` OTel resource attribute |
| `obs.service.namespace` | string | yes | — | Maps to `service.namespace` OTel resource attribute |
| `obs.scrape` | `"true"` | no | — | Opt in to Prometheus scraping |
| `obs.scrape.port` | port number string | no | `8080` | Container port exposing `/metrics` |
| `obs.scrape.path` | path string | no | `/metrics` | Metrics path |

---

## Retention

| Store | Retention |
|-------|-----------|
| Loki (logs) | 14 days |
| Prometheus (metrics) | 30 days |
| Jaeger (traces) | 7 days |

---

## Network

```
network name:   observability
driver:         bridge
join command:   add to service networks list + declare as external
```

---

## Opt-in Snippet

Minimal docker-compose fragment to connect a service:

```yaml
services:
  my-service:
    image: my-org/my-service:latest
    labels:
      obs.service.name: "my-service"
      obs.service.namespace: "my-project"
      obs.scrape: "true"
      obs.scrape.port: "8080"
    environment:
      OTEL_EXPORTER_OTLP_ENDPOINT: "http://otel-collector:4317"
      OTEL_EXPORTER_OTLP_PROTOCOL: "grpc"
      OTEL_SERVICE_NAME: "my-service"
      OTEL_RESOURCE_ATTRIBUTES: "service.namespace=my-project"
    networks:
      - app
      - observability

networks:
  observability:
    name: observability
    external: true
  app:
    driver: bridge
```

---

## Readiness Checks

| Service | Readiness URL | Expected |
|---------|--------------|----------|
| Loki | `GET http://loki:3100/ready` | body: `ready` |
| Prometheus | `GET http://prometheus:9090/-/ready` | body: `Prometheus Server is Ready.` |
| Jaeger | `GET http://jaeger:16686` | HTTP 200 |
| Grafana | `GET http://grafana:3003/api/health` | JSON `{"database":"ok"}` |
| OTel Collector | `GET http://otel-collector:13133` | HTTP 200 |

---

## Management Commands

```bash
./obs.sh up -d              # start stack
./obs.sh down               # stop stack
./obs.sh pull               # pull new images
./obs.sh logs -f <service>  # follow logs
./backup.sh                 # backup all volumes to ./backups/
```

---

## Grafana API (scripting)

```bash
# List dashboards
curl -u admin:$GRAFANA_ADMIN_PASSWORD http://localhost:3003/api/search

# Query Loki
curl http://localhost:3100/loki/api/v1/query_range \
  --data-urlencode 'query={service_namespace="my-project"}' \
  --data-urlencode 'start=1h' \
  --data-urlencode 'limit=100'

# Query Prometheus
curl 'http://localhost:9090/api/v1/query?query=up'
```

---

## Dashboards

| UID | Title | Purpose |
|-----|-------|---------|
| `obs-projects` | Observability — All Projects | Unified, one row per project (namespace), repeat row pattern |
| `obs-overview` | Observability — Overview | Cross-project aggregate view |
| `obs-namespace` | Observability — Namespace | Single namespace drill-down with service filter |

Auth: anonymous access disabled. Missing MCP_API_KEY rejects tool requests. Basic auth required (admin / `GRAFANA_ADMIN_PASSWORD`).

---

## MCP Server

Agents can query the observability stack directly via MCP without knowing the underlying APIs.

**Endpoint:** `http://localhost:8000/mcp`
**Transport:** Streamable HTTP (MCP spec 2025-03-26)
**Auth:** `Authorization: Bearer <MCP_API_KEY>`

### Connecting (`.mcp.json`)

```json
{
  "mcpServers": {
    "observability": {
      "type": "http",
      "url": "http://localhost:8000/mcp",
      "headers": {
        "Authorization": "Bearer <MCP_API_KEY>"
      }
    }
  }
}
```

### Tools

#### Discovery

| Tool | Description |
|------|-------------|
| `list_projects()` | All observed namespaces — use these as the `project` arg everywhere |
| `list_services(project)` | All service names within a namespace — use these as the `service` arg |
| `list_trace_services()` | All service names that have sent traces to Jaeger |
| `get_stack_health()` | Health of Prometheus, Loki, Jaeger, OTel Collector |

#### Logs

| Tool | Args | Description |
|------|------|-------------|
| `query_logs` | `project`, `search?`, `service?`, `level?`, `regex?`, `range_minutes?`, `limit?` | Loki logs with optional filters. Results include `time`, `service`, `log`, and (when available) `level` and `trace_id`. |
| `get_recent_errors` | `project`, `service?`, `range_minutes?`, `limit?` | error/exception/fatal/panic lines |
| `query_logs_raw` | `logql`, `range_minutes?`, `limit?` | Arbitrary LogQL query — use when other tools aren't expressive enough |

**Recommended log query workflow:**
1. `list_projects()` → find namespace
2. `list_services(project)` → find service name
3. `query_logs(project, service="api", level="error")` → filtered logs
4. `query_logs_raw('{service_namespace="x"} | json | field="value"')` → if you need LogQL pipeline operators

**Log level values:** `debug`, `info`, `warn`, `error` (only populated if the app sets the `level` label or uses structured logging)

#### Metrics

| Tool | Args | Description |
|------|------|-------------|
| `query_metrics` | `promql` | Instant PromQL query — current value snapshot |
| `query_metrics_range` | `promql`, `range_minutes?`, `step_seconds?` | Time series PromQL — use to see trends, spikes, patterns |

#### Traces

| Tool | Args | Description |
|------|------|-------------|
| `get_traces` | `service`, `operation?`, `limit?`, `min_duration_ms?` | Recent Jaeger traces. `service` = `obs.service.name` label (use `list_trace_services()` to find). Returns `traceId`, `operation`, `duration_ms`, `has_errors`, `error_operations`. |

#### Project overview

| Tool | Args | Description |
|------|------|-------------|
| `get_project_health` | `project` | Log count, error count, error rate %, trace presence — last 1h |
| `get_container_health` | `namespace?` | HTTP health probe status from blackbox exporter |

### Generating an API key

```bash
openssl rand -hex 32
```

Set as `MCP_API_KEY` in GitLab CI/CD variables (masked, protected). It flows into `.env` on each deploy.

---

## File Layout

```
/opt/observability/
├── docker-compose.yml            main stack definition
├── otel-collector-config.yaml    receiver / processor / exporter config
├── loki-config.yaml              loki single-binary config
├── prometheus.yml                prometheus scrape config
├── grafana/
│   └── provisioning/
│       ├── datasources/          prometheus.yml, loki.yml, jaeger.yml
│       └── dashboards/           provider.yml, projects.json, overview.json, namespace.json
├── mcp-server/
│   ├── server.py                 MCP server (FastMCP, Streamable HTTP)
│   ├── requirements.txt
│   └── Dockerfile
├── obs.sh                        management wrapper
├── observability.service         systemd unit
├── backup.sh                     volume backup script
├── tests/
│   ├── docker-compose.test.yml   E2E test harness
│   ├── test-otel-emitter.yaml    synthetic signal emitter
│   └── smoke-test.sh             readiness + API smoke tests
├── .env                          secrets (gitignored)
└── .env.example                  template
```
