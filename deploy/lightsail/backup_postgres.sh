#!/usr/bin/env sh
set -eu

BACKUP_DIR="${BACKUP_DIR:-./backups}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"

docker compose exec -T db pg_dump \
  -U "${POSTGRES_USER:-pipe1}" \
  -d "${POSTGRES_DB:-pipe1_license}" \
  > "$BACKUP_DIR/pipe1_license_$TIMESTAMP.sql"
