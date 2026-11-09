# Troubleshooting

## Stack won't start

```bash
# Check which service is failing
./obs.sh ps
./obs.sh logs

# Check a specific service
./obs.sh logs otel-collector
./obs.sh logs loki
```

**Port conflict**: if a port is already in use, edit `.env` to change `GRAFANA_PORT`, or edit `docker-compose.yml` host ports.

**Permission denied on Docker socket**: the otel-collector and prometheus services need read access to `/var/run/docker.sock`. Ensure the Docker socket is readable by the container user, or add the host user to the `docker` group.

---

## No metrics appearing in Prometheus / Grafana

1. Check Prometheus targets: http://localhost:9090/targets
   - If the `docker` job shows no targets → the container is missing `obs.scrape: "true"` label
   - If the target is `DOWN` → wrong port (`obs.scrape.port`) or the container isn't exposing `/metrics`

2. Verify labels on the container:
   ```bash
   docker inspect <container_name> | jq '.[0].Config.Labels'
   ```

3. Confirm the container is on the `observability` network:
   ```bash
   docker network inspect observability | jq '.[0].Containers'
   ```

---

## No logs appearing in Loki / Grafana

1. Check the OTel Collector is running and healthy:
   ```bash
   curl http://localhost:13133   # should return 200
   ./obs.sh logs otel-collector | grep -i error
   ```

2. Confirm the `file_log` receiver has access to Docker logs:
   - The otel-collector container must mount `/var/lib/docker/containers` as `:ro`
   - Check `docker-compose.yml` volumes section for `otel-collector`

3. Query Loki directly to rule out a Grafana config issue:
   ```bash
   curl 'http://localhost:3100/loki/api/v1/labels'
   curl 'http://localhost:3100/loki/api/v1/query?query={service_namespace="my-project"}'
   ```

---

## No traces appearing in Jaeger

1. Confirm your application is sending OTLP to `otel-collector:4317` (not directly to Jaeger)
2. Check the OTel Collector trace pipeline:
   ```bash
   ./obs.sh logs otel-collector | grep -i trace
   ```
3. Verify Jaeger is healthy: http://localhost:16686
4. Check Jaeger logs for Badger storage errors:
   ```bash
   ./obs.sh logs jaeger
   ```

---

## Grafana datasources show red / unhealthy

1. Check each backend is reachable from inside Grafana:
   ```bash
   docker exec $(docker compose -f /opt/observability/docker-compose.yml ps -q grafana) \
     wget -qO- http://prometheus:9090/-/ready
   ```

2. All services must be on the `observability` network — verify with:
   ```bash
   docker network inspect observability
   ```

3. Try restarting Grafana after the backends are healthy:
   ```bash
   ./obs.sh restart grafana
   ```

---

## High memory usage

The OTel Collector has a `memory_limiter` processor set to 512 MiB. If the host is under memory pressure:

```yaml
# otel-collector-config.yaml — increase limit
processors:
  memory_limiter:
    limit_mib: 256   # lower if needed
```

Then `./obs.sh restart otel-collector`.

---

## Volume data loss / corruption

1. Check volume contents:
   ```bash
   docker run --rm -v obs_loki-data:/data alpine ls /data
   ```

2. Restore from backup:
   ```bash
   # Stop the stack first
   ./obs.sh down

   # Restore a volume
   docker run --rm \
     -v obs_loki-data:/data \
     -v ./backups:/backup \
     alpine \
     sh -c "rm -rf /data/* && tar xzf /backup/obs_loki-data-YYYYMMDD.tar.gz -C /data"

   ./obs.sh up -d
   ```

---

## Systemd service fails to start

```bash
sudo systemctl status observability
sudo journalctl -u observability -n 50

# Common fix: Docker isn't ready yet
# Add a delay to the unit or ensure docker.service is fully up:
sudo systemctl restart docker
sudo systemctl start observability
```

---

## Running the smoke tests

```bash
# Ensure the main stack is up first
./obs.sh up -d

# Run the E2E test harness
docker compose -f tests/docker-compose.test.yml up --abort-on-container-exit

# Clean up
docker compose -f tests/docker-compose.test.yml down -v
```
