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

# Upload to S3 (optional — requires S3_BACKUP_BUCKET env var and aws CLI)
if [ -n "${S3_BACKUP_BUCKET:-}" ]; then
    if command -v aws &>/dev/null; then
        # Note: the S3 bucket should have a lifecycle rule for retention;
        # the local 7-day cleanup below does not apply to S3 objects.
        if aws s3 cp "$BACKUP_FILE" "s3://${S3_BACKUP_BUCKET}/econ-newsfeed/" --sse AES256 --quiet; then
            echo "Uploaded to s3://${S3_BACKUP_BUCKET}/econ-newsfeed/"
        else
            echo "WARNING: S3 upload failed" >&2
        fi
    else
        echo "WARNING: aws CLI not found, skipping S3 upload" >&2
    fi
fi

# Delete backups older than retention period
DELETED=$(find "$BACKUP_DIR" -name "econ_newsfeed_*.sql.gz" -mtime +$RETENTION_DAYS -delete -print | wc -l)
if [ "$DELETED" -gt 0 ]; then
    echo "Deleted $DELETED backup(s) older than $RETENTION_DAYS days"
fi
