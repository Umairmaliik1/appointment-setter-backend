#!/usr/bin/env bash
set -euo pipefail

: "${POSTGRES_HOST:=localhost}"
: "${POSTGRES_PORT:=5432}"
: "${POSTGRES_DB:=appointment_setter}"
: "${POSTGRES_USER:=postgres}"
: "${BACKUP_DIR:=./backups}"

mkdir -p "$BACKUP_DIR"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_FILE="$BACKUP_DIR/${POSTGRES_DB}_${TIMESTAMP}.sql.gz"

PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_dump \
  --host "$POSTGRES_HOST" \
  --port "$POSTGRES_PORT" \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --format=plain \
  --no-owner \
  --no-acl | gzip > "$OUT_FILE"

echo "Backup written: $OUT_FILE"
