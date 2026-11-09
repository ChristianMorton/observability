#!/bin/sh
# smoke-test.sh — validates that the full observability stack is reachable
# and accepting data. Exits 0 on success, 1 on any failure.
set -e

WAIT=30   # seconds to wait for services to be ready
PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS+1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

check_url() {
  local desc="$1"
  local url="$2"
  local expect="$3"

  response=$(curl -sf --max-time 10 "$url" 2>&1) || { fail "$desc — curl failed ($url)"; return; }
  if echo "$response" | grep -q "$expect"; then
    pass "$desc"
  else
    fail "$desc — expected '$expect' in response from $url"
  fi
}

echo "Waiting ${WAIT}s for services to be ready..."
sleep "$WAIT"

echo ""
echo "=== Observability Stack Smoke Tests ==="
echo ""

echo "--- Backend readiness ---"
check_url "Loki ready"       "http://loki:3100/ready"            "ready"
check_url "Prometheus ready" "http://prometheus:9090/-/ready"    "Prometheus Server is Ready"
check_url "Jaeger UI"        "http://jaeger:16686"               "Jaeger"
check_url "Grafana ping"     "http://grafana:3003/api/health"    "ok"

echo ""
echo "--- OTel Collector health ---"
check_url "OTel health_check" "http://otel-collector:13133"  "Server available"

echo ""
echo "--- Prometheus targets ---"
check_url "Prometheus targets API" "http://prometheus:9090/api/v1/targets" "activeTargets"

echo ""
echo "--- Loki labels (after ingestion) ---"
check_url "Loki labels API" "http://loki:3100/loki/api/v1/labels" "data"

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
[ "$FAIL" -eq 0 ]
