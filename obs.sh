#!/bin/bash
# obs.sh — management wrapper for the observability stack
# Usage: obs.sh [docker compose arguments]
#   obs.sh up -d          start in background
#   obs.sh down           stop and remove containers
#   obs.sh logs -f        follow logs
#   obs.sh ps             list running services
#   obs.sh pull           pull latest images

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

exec docker compose "$@"
