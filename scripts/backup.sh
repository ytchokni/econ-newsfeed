#!/usr/bin/env bash
set -euo pipefail
umask 077  # dumps hold the whole database

# Daily MySQL backup for econ-newsfeed
# Cron: 0 3 * * * /opt/econ-newsfeed/scripts/backup.sh >> /backups/backup.log 2>&1
#
# The log MUST live somewhere the ubuntu user can write (/backups, not
# /var/log) — a failing redirect kills the cron job before this script
# even starts, which silently disabled backups until 2026-06-11.
#
# Disk safety: on 2026-08-27 eight ~2GB dumps (7-day mtime retention) filled
# the disk mid-dump. That run left its 2GB .partial behind and `set -e` exited
# before the retention step, so every later run failed the same way and MySQL
# had no free space for a month. Hence: count-based retention that runs only
# after a verified dump (failed runs never delete a good backup), a trap that
# removes this run's .partial, stale-.partial cleanup before anything else is
# written, timeouts so a hung dump can't hold the lock, and a free-space check
# that skips the dump (with an ERROR in the log) rather than fill the disk.
#
# Requires: MYSQL_ROOT_PASSWORD set in environment or sourced from .env

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" >/dev/null && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
KEEP_BACKUPS="${KEEP_BACKUPS:-3}"             # newest verified dumps kept locally
MIN_FREE_MB="${MIN_FREE_MB:-2048}"            # headroom left for MySQL after the dump
FALLBACK_DUMP_MB="${FALLBACK_DUMP_MB:-3072}"  # size estimate when no previous dump exists
DUMP_TIMEOUT_MIN="${DUMP_TIMEOUT_MIN:-120}"   # the dump normally takes ~3 minutes

PARTIAL_FILE=""
DUMP_START=""
cleanup() {
    local rc=$?
    if [ -n "$PARTIAL_FILE" ] && [ -e "$PARTIAL_FILE" ]; then
        rm -f -- "$PARTIAL_FILE"
        echo "Removed incomplete dump: $PARTIAL_FILE" >&2
    fi
    if [ -n "$DUMP_START" ]; then
        local elapsed=$(( SECONDS - DUMP_START ))
        if [ "$rc" -eq 141 ]; then
            echo "ERROR: dump outgrew the free space (would cut into MIN_FREE_MB=${MIN_FREE_MB})" >&2
        elif { [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; } && [ "$elapsed" -ge $(( DUMP_TIMEOUT_MIN * 60 )) ]; then
            echo "ERROR: dump exceeded DUMP_TIMEOUT_MIN=${DUMP_TIMEOUT_MIN} and was killed" >&2
        elif [ "$rc" -eq 137 ]; then
            echo "ERROR: dump was SIGKILLed after ${elapsed}s (db container OOM or restart?)" >&2
        fi
    fi
    if [ "$rc" -ne 0 ]; then
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Backup FAILED (exit $rc)" >&2
    fi
}
trap cleanup EXIT
trap 'exit 1' INT TERM HUP

if ! [[ "$KEEP_BACKUPS" =~ ^[1-9][0-9]{0,2}$ ]] \
    || ! [[ "$MIN_FREE_MB" =~ ^(0|[1-9][0-9]{0,6})$ ]] \
    || ! [[ "$FALLBACK_DUMP_MB" =~ ^[1-9][0-9]{0,6}$ ]] \
    || ! [[ "$DUMP_TIMEOUT_MIN" =~ ^[1-9][0-9]{0,4}$ ]]; then
    echo "ERROR: KEEP_BACKUPS (1-999), MIN_FREE_MB, FALLBACK_DUMP_MB and DUMP_TIMEOUT_MIN" \
         "must be plain whole numbers (no leading zeros)" >&2
    exit 1
fi

# Source .env if MYSQL_ROOT_PASSWORD not already set
if [ -z "${MYSQL_ROOT_PASSWORD:-}" ] && [ -f "$PROJECT_DIR/.env" ]; then
    export "$(grep '^MYSQL_ROOT_PASSWORD=' "$PROJECT_DIR/.env" | head -1)"
fi

if [ -z "${MYSQL_ROOT_PASSWORD:-}" ]; then
    echo "ERROR: MYSQL_ROOT_PASSWORD not set" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"

# One run at a time: a manual run overlapping the cron job would need twice
# the disk and could delete the other run's .partial as "stale". Locking the
# directory itself means no lock file whose owner could block other users.
exec 9<"$BACKUP_DIR"
if ! flock -n 9; then
    echo "ERROR: another backup is already running" >&2
    exit 1
fi

# Leftovers from a run killed outright (OOM, reboot), which the trap never got
# to clean up. This comes before the first log line: on a 100% full disk that
# write fails and `set -e` would stop the run every night.
find -H "$BACKUP_DIR" -maxdepth 1 -type f -name 'econ_newsfeed_*.sql.gz.partial' -print -delete \
    | sed 's/^/Removed stale partial: /'

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Starting backup" || true

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/econ_newsfeed_${TIMESTAMP}.sql.gz"

# Verified dumps, newest first. Only names this script creates count, so a
# hand-made file can't take a retention slot or skew the size estimate.
list_backups() {
    find -H "$BACKUP_DIR" -maxdepth 1 -type f \
        -name 'econ_newsfeed_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9][0-9][0-9][0-9][0-9].sql.gz' \
        -printf '%f\n' | LC_ALL=C sort -r
}

DB_CONTAINER=$(timeout -k 10s 2m docker ps -q --filter label=com.docker.compose.project=econ-newsfeed \
    --filter label=com.docker.compose.service=db)
if ! [[ "$DB_CONTAINER" =~ ^[0-9a-f]+$ ]]; then
    echo "ERROR: expected exactly one econ-newsfeed db container, found: '${DB_CONTAINER:-none}'" >&2
    exit 1
fi

# Size the next dump from the largest kept one (not the newest, which could be
# unusually small) and refuse to start unless MySQL keeps MIN_FREE_MB free.
LARGEST_BYTES=$(list_backups | while read -r f; do stat -c %s "$BACKUP_DIR/$f"; done | sort -n | tail -1)
if [ -n "$LARGEST_BYTES" ]; then
    EST_MB=$(( LARGEST_BYTES * 12 / 10 / 1048576 + 1 ))
else
    EST_MB=$FALLBACK_DUMP_MB
fi
AVAIL_MB=$(df -B1 --output=avail "$BACKUP_DIR" | awk 'NR == 2 && $1 ~ /^[0-9]+$/ {print int($1 / 1048576)}')
if [ -z "$AVAIL_MB" ]; then
    echo "ERROR: could not read free space for $BACKUP_DIR — backup skipped" >&2
    exit 1
fi
if [ "$AVAIL_MB" -lt $(( EST_MB + MIN_FREE_MB )) ]; then
    echo "ERROR: only ${AVAIL_MB}MB free in $BACKUP_DIR; need ~${EST_MB}MB for the dump" \
         "plus ${MIN_FREE_MB}MB headroom for MySQL — backup skipped" >&2
    exit 1
fi

# Dump to a .partial file and only promote it after verifying the
# '-- Dump completed' trailer. A mysqldump that dies mid-stream (e.g. the
# db container under memory pressure on the html blob tables) still leaves
# a gzip-valid file — without this check a truncated dump silently becomes
# the "latest backup" (happened on 2026-06-01: the dump lacked researchers
# and papers entirely).
# `-e MYSQL_PWD` with no value forwards it from this environment, keeping the
# password out of `ps` output. The inner timeout stops mysqldump itself (killing
# the docker CLI leaves it running in the container); the outer one is a
# backstop for a stuck CLI, run in the foreground so Ctrl-C still reaches it.
# `head -c` caps the file at the space the check above allowed, so a dump that
# grew past the estimate aborts (SIGPIPE) instead of eating MySQL's headroom.
PARTIAL_FILE="$BACKUP_FILE.partial"
DUMP_START=$SECONDS
MYSQL_PWD="$MYSQL_ROOT_PASSWORD" timeout --foreground -k 1m "$(( DUMP_TIMEOUT_MIN + 5 ))m" \
    docker exec -e MYSQL_PWD "$DB_CONTAINER" \
    timeout -k 1m "${DUMP_TIMEOUT_MIN}m" mysqldump -u root --single-transaction --quick econ_newsfeed \
    | gzip | head -c "$(( (AVAIL_MB - MIN_FREE_MB) * 1048576 ))" > "$PARTIAL_FILE"

if ! gunzip -c "$PARTIAL_FILE" | tail -1 | grep -q '^-- Dump completed'; then
    echo "ERROR: dump truncated (no '-- Dump completed' trailer) — backup discarded" >&2
    exit 1
fi
mv "$PARTIAL_FILE" "$BACKUP_FILE"

echo "Backup created: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))" || true

# Upload to S3 (optional — requires S3_BACKUP_BUCKET env var and aws CLI)
if [ -n "${S3_BACKUP_BUCKET:-}" ]; then
    if command -v aws &>/dev/null; then
        # Note: the S3 bucket should have a lifecycle rule for retention;
        # the local cleanup below does not apply to S3 objects.
        if aws s3 cp "$BACKUP_FILE" "s3://${S3_BACKUP_BUCKET}/econ-newsfeed/" --sse AES256 --quiet; then
            echo "Uploaded to s3://${S3_BACKUP_BUCKET}/econ-newsfeed/"
        else
            echo "WARNING: S3 upload failed" >&2
        fi
    else
        echo "WARNING: aws CLI not found, skipping S3 upload" >&2
    fi
fi

# Keep the newest $KEEP_BACKUPS verified dumps, always including this run's
# (a clock-skewed, future-dated name must not push it out).
list_backups | awk -v keep="$KEEP_BACKUPS" -v self="${BACKUP_FILE##*/}" '$0 != self && ++n >= keep' \
    | while read -r old; do
        rm -f -- "$BACKUP_DIR/$old"
        echo "Deleted old backup: $old" || true
    done
