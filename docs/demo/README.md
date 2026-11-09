# Synthetic dashboard preview

Run `docker compose -f docs/demo/compose.yml up -d` from the repository root.
Open http://localhost:43300/d/demo-obs-host/ or http://localhost:43300/d/demo-obs-collector/.
Stop with the same Compose file and `down`.

These are copies of the repository's host and collector dashboards, with queries replaced by Grafana's built-in TestData random walks. A visible banner identifies every screenshot as synthetic. This demonstrates dashboard layout, not a connected collector or verified signal delivery. No live telemetry or private configuration is used. The production dashboard queries are unchanged.

The screenshots were captured from official Grafana OSS 11.3.1 running locally on Windows because Docker was unavailable. The Compose preview is provided for reproduction; that container command has only passed configuration validation here. The recording navigates the two real dashboard pages.
