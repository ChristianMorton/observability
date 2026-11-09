# Public release preparation

Prepared 2026-11-09. This is a clean source edition, not the private deployment configuration.

- Replaced personal domains and tailnet addresses with documentation examples.
- Removed live-site probe inventories and the personal hub override.
- Replaced auto-deploy GitLab CI with offline validation. No existing deployment was changed.
- Bound base host ports to loopback; require configured Grafana secrets and MCP key in Compose.
- MCP tools fail closed without a key, with constant-time token comparison.
- Default public-project allowlist is empty; public stack health is still available.
- Added YAML/JSON/Python structure validation and public setup documentation.

History review examined 462 reachable Git objects with patterns for private keys, GitLab/GitHub tokens and AWS access IDs: no matches. This limited pattern check cannot certify absence of all credentials. Historical commits retain personal infrastructure details; publish the clean source archive with new history rather than exposing this private repository's history.

Docker Desktop's Linux engine was unavailable on the preparation machine. Runtime ingestion, dashboard behaviour, container image validation, backup restoration and smoke tests require a Linux Docker host before claiming a verified release. Configuration syntax and authentication regression results are recorded in the accompanying release report.

Before publishing: confirm ownership and choose a licence. Do not copy private `.env`, telemetry, backups, dashboard exports containing credentials, or Git history into the public repository.

Validation: 33 YAML/JSON/Python files passed offline parsing; base Docker Compose configuration passed with local test values. No runtime deployment was attempted.

Authentication regression: 2 tests passed (configured-key acceptance, invalid-key rejection, missing-key rejection and public liveness).

## Portfolio media

Added host and collector screenshots, an animated GIF and an MP4 dashboard walkthrough. Captured from official Grafana OSS 11.3.1 running locally with its TestData source; no real telemetry. The preview dashboard copies and Docker reproduction configuration live under docs/demo. Production dashboards are unchanged. Capture demonstrates actual Grafana rendering, not signal ingestion. Inspected screenshots and decoded the recording.
