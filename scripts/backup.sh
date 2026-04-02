#!/usr/bin/env bash
set -euo pipefail

# Daily MySQL backup for econ-newsfeed
# Cron: 0 3 * * * /opt/econ-newsfeed/scripts/backup.sh
#
# Requires: MYSQL_ROOT_PASSWORD set in environment or sourced from .env

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION_DAYS=7

# Source .env if MYSQL_ROOT_PASSWORD not already set
if [ -z "${MYSQL_ROOT_PASSWORD:-}" ] && [ -f "$PROJECT_DIR/.env" ]; then
    export "$(grep '^MYSQL_ROOT_PASSWORD=' "$PROJECT_DIR/.env" | head -1)"
fi

if [ -z "${MYSQL_ROOT_PASSWORD:-}" ]; then
    echo "ERROR: MYSQL_ROOT_PASSWORD not set" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/econ_newsfeed_${TIMESTAMP}.sql.gz"

DB_CONTAINER=$(docker ps -qf "name=db" | head -1)
if [ -z "$DB_CONTAINER" ]; then
    echo "ERROR: MySQL container not found" >&2
    exit 1
fi

docker exec "$DB_CONTAINER" mysqldump -u root -p"$MYSQL_ROOT_PASSWORD" econ_newsfeed | gzip > "$BACKUP_FILE"

echo "Backup created: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"

# Delete backups older than retention period
DELETED=$(find "$BACKUP_DIR" -name "econ_newsfeed_*.sql.gz" -mtime +$RETENTION_DAYS -delete -print | wc -l)
if [ "$DELETED" -gt 0 ]; then
    echo "Deleted $DELETED backup(s) older than $RETENTION_DAYS days"
fi
