#!/usr/bin/env bash
# Bring up the satellite collector in the CURRENT user's rootless Docker daemon.
# Usage: SAT_PORT=303 SAT_ENV=apps ./satellite-up.sh
set -euo pipefail
cd "$(dirname "$0")"
uid=$(id -u)
if [ -S "/run/user/$uid/docker.sock" ]; then
  export DOCKER_HOST="unix:///run/user/$uid/docker.sock"
  export DOCKER_SOCK="/run/user/$uid/docker.sock"
  export DOCKER_DATA="$HOME/.local/share/docker"
fi
: "${SAT_PORT:?SAT_PORT required (e.g. 303 for apps)}"
export SAT_ENV="${SAT_ENV:-$(id -un)}"
export SAT_HOST="${SAT_HOST:-$(hostname -s)}"
docker network inspect observability >/dev/null 2>&1 || docker network create observability
docker compose -f docker-compose.satellite.yml up -d --remove-orphans
