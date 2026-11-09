# Connecting a Project to the Observability Stack

Any Docker Compose project on the same host can opt in by:

1. Joining the `observability` Docker network
2. Adding `obs.*` labels to its containers

No changes to the observability stack itself are needed.

---

## First: which host is your project on?

**This matters more than anything else on this page.** The stack itself runs only on
**prod** (`ubuntu-4gb-nbg1-1`, tailnet `192.0.2.10`). The label mechanism below is
*host-local*: apps do not push their logs, the collector **pulls** them by watching the
local Docker socket and tailing `/var/lib/docker/containers/<id>/*.log`. Prometheus
likewise finds scrape targets via `docker_sd` against the local socket. Neither can see
another machine.

So the labels are not a request that gets routed somewhere — they are a filter applied
to containers the collector can already see.

| Your project runs on… | What to do |
|---|---|
| **prod** (`192.0.2.10`) | Follow the steps below as written. |
| **apps** (`192.0.2.10`) — osint-map, gratitude-app, staging the-digital-local | Same steps, **but** the satellite collector must be running on that host first (`docker-compose.satellite.yml` in this repo, deployed by the `deploy:satellite` CI job). It creates the `observability` network there and forwards to prod over the tailnet. Note the caveats below. |
| anywhere else | Neither works. You need OTLP push to prod's collector over the tailnet, or a satellite of your own. |

### Caveats on the apps box

- **`obs.scrape.*` labels do nothing.** Prometheus lives on prod and cannot discover
  targets on another host. Push metrics over OTLP instead of exposing `/metrics`.
- **`obs.health.*` labels do nothing.** The blackbox exporter is on prod and cannot
  reach apps-box containers by Docker DNS. Setting them looks like monitoring and
  isn't. (See the failure mode below for why a *wrong* health signal is worse than none.)
- Logs and OTLP traces/metrics work normally.

### Gotcha: a container with no exposed port is never discovered

`docker_observer` builds its endpoint list from container **ports**. A container that
exposes none produces no endpoint, so `receiver_creator` never matches it and its logs
are never tailed — no error, no warning, just silence. The labels look right and
nothing happens.

Almost every real service exposes something (`EXPOSE` in its Dockerfile counts, even
with no host publish), so this rarely bites in production — but a sidecar, a cron-style
one-shot, or a pure worker container will vanish silently. If a container's logs are
missing, check `docker inspect <c> --format '{{.Config.ExposedPorts}}'` before
suspecting the collector.

### Beware: the shared network is a flat namespace

Compose registers a container's **service name** as a DNS alias on every network it
joins, and `observability` is shared by every project on the host. Two projects that
both have a service called `web` or `api` will collide, and Docker DNS silently
resolves to whichever it likes.

This is not hypothetical. Prod's gratitude nginx proxied to `web:3000` and — once
the-digital-local joined the same network — began serving **Trivialism's page**
instead, while its blackbox health probe went green off the wrong service. It ran that
way for weeks.

Two rules follow:

1. **Attach the minimum.** Only containers that actually *push OTLP* need to be on the
   network. Log collection needs labels only, never membership. See
   `osint-map/docker-compose.observability.yml` for a worked example.
2. **Add an explicit unambiguous alias** (`aliases: [myproject-api]`) for anything you
   attach, and have other containers use that name — never the bare service name.

---

## Step 1 — Join the Shared Network

Add the `observability` network as an **external** network in your project's `docker-compose.yml`:

```yaml
networks:
  observability:
    name: observability
    external: true   # network is owned by the observability stack
  app:              # your project's own network (optional)
    driver: bridge
```

Attach each service you want to observe:

```yaml
services:
  my-api:
    image: my-org/my-api:latest
    networks:
      - app
      - observability
```

---

## Step 2 — Add Container Labels

### Required labels

| Label | Example | Description |
|-------|---------|-------------|
| `obs.service.name` | `my-api` | Service name — used as `service_name` in logs/metrics/traces |
| `obs.service.namespace` | `my-project` | Namespace (project name) — groups services in dashboards |

### Optional labels

| Label | Default | Description |
|-------|---------|-------------|
| `obs.scrape` | — | Set to `"true"` to have Prometheus scrape this container |
| `obs.scrape.port` | `8080` | Port that exposes `/metrics` |
| `obs.scrape.path` | `/metrics` | Path for Prometheus scraping |

### Example service definition

```yaml
services:
  my-api:
    image: my-org/my-api:latest
    labels:
      obs.service.name: "my-api"
      obs.service.namespace: "my-project"
      obs.scrape: "true"
      obs.scrape.port: "8080"
      obs.scrape.path: "/metrics"
    networks:
      - app
      - observability
```

---

## Step 3 — Send Traces and Logs via OTLP

Configure your application's OpenTelemetry SDK to export to the collector:

| Protocol | Endpoint |
|----------|----------|
| OTLP gRPC | `otel-collector:4317` (from within the observability network) |
| OTLP HTTP | `otel-collector:4318` (from within the observability network) |
| OTLP gRPC (host) | `localhost:4317` (from the host machine) |
| OTLP HTTP (host) | `localhost:4318` (from the host machine) |

### Environment variable configuration (SDK-agnostic)

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_EXPORTER_OTLP_PROTOCOL=grpc
OTEL_SERVICE_NAME=my-api
OTEL_RESOURCE_ATTRIBUTES=service.namespace=my-project
```

### Docker Compose example with env vars

```yaml
services:
  my-api:
    image: my-org/my-api:latest
    environment:
      OTEL_EXPORTER_OTLP_ENDPOINT: "http://otel-collector:4317"
      OTEL_EXPORTER_OTLP_PROTOCOL: "grpc"
      OTEL_SERVICE_NAME: "my-api"
      OTEL_RESOURCE_ATTRIBUTES: "service.namespace=my-project"
    labels:
      obs.service.name: "my-api"
      obs.service.namespace: "my-project"
      obs.scrape: "true"
      obs.scrape.port: "8080"
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

## What Happens Automatically

Once a container joins the network and has the required labels:

- **Logs**: The OTel Collector tails Docker container log files via `file_log` receiver and forwards them to Loki with `service_name` and `service_namespace` labels.
- **Traces**: Any OTLP trace data sent to `otel-collector:4317/4318` is forwarded to Jaeger.
- **Metrics**: If `obs.scrape=true`, Prometheus discovers and scrapes the container. OTLP metrics are also accepted and forwarded.
- **Grafana**: The namespace dashboard's dropdown auto-populates with your `service_namespace` once data arrives.

---

## Removing from the Observability Stack

Simply remove the `observability` network from your service and remove the `obs.*` labels. The stack continues running unaffected.


## Satellites on hub (2026-10)

Every hub user runs its own rootless Docker, so each has a satellite in
`~/observability-satellite` (otel-collector + alloy log shipper):

| user    | SAT_PORT | SAT_ENV  | loopback ports      |
|---------|----------|----------|---------------------|
| hub     | 103      | hub-dev  | 10317/10318/10133   |
| staging | 203      | staging  | 20317/20318/20133   |
| apps    | 303      | apps     | 30317/30318/30133   |
| prod    | -        | prod     | (pending)           |

Deploy/update (as that user, from a copy of this repo's satellite files):

    SAT_PORT=303 SAT_HOST=hub SAT_ENV=apps ./satellite-up.sh

What you get with **no app changes**: every container's stdout/stderr in Loki
(`service_namespace` = obs label > compose project; `service_name` = obs label >
compose service), and per-container CPU/memory/network/restarts in Prometheus
(`otel_container_*`, labels `compose_project`, `compose_service`, `deployment_environment`).

For traces / app metrics: join the external `observability` network and set
`OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317` (grpc) or
`http://otel-collector:4318` (http). Uptime probes and alert rules live in
`prometheus.yml` (`blackbox-public`, `blackbox-tailnet`) and `prometheus-rules/`;
alert e-mail delivery is described in `alerting/README.md`.
