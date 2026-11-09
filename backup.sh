#!/bin/bash
# backup.sh — nightly backup of all observability named volumes
# Usage: ./backup.sh
# Recommended: add to cron with:
#   0 2 * * * /opt/observability/backup.sh >> /var/log/observability-backup.log 2>&1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="$SCRIPT_DIR/backups"
DATE=$(date +%Y%m%d)
KEEP=7  # number of daily backups to retain per volume

VOLUMES=(
  obs_loki-data
  obs_prometheus-data
  obs_jaeger-data
  obs_grafana-data
)

mkdir -p "$BACKUP_DIR"

backup_volume() {
  local volume="$1"
  local dest="$BACKUP_DIR/${volume}-${DATE}.tar.gz"

  echo "[$(date -u +%FT%TZ)] Backing up $volume → $dest"
  docker run --rm \
    -v "${volume}:/data:ro" \
    -v "${BACKUP_DIR}:/backup" \
    alpine \
    tar czf "/backup/${volume}-${DATE}.tar.gz" -C /data .
  echo "[$(date -u +%FT%TZ)] Done: $dest ($(du -sh "$dest" | cut -f1))"

  # Retain only the $KEEP most recent backups for this volume
  local old_backups
  old_backups=$(ls -t "$BACKUP_DIR/${volume}-"*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)))
  if [[ -n "$old_backups" ]]; then
    echo "$old_backups" | xargs -r rm --
    echo "[$(date -u +%FT%TZ)] Pruned old backups for $volume"
  fi
}

echo "[$(date -u +%FT%TZ)] === Observability backup started ==="
for vol in "${VOLUMES[@]}"; do
  backup_volume "$vol"
done
echo "[$(date -u +%FT%TZ)] === Backup complete ==="
