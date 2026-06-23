#!/usr/bin/env bash
set -euo pipefail

# Refresh the local MySQL database from the latest production backup.
#
#   make sync-prod          # or: ./scripts/sync_prod_db.sh
#
# DESTRUCTIVE to local data: drops the local docker volume and rebuilds it
# from the newest /backups/econ_newsfeed_*.sql.gz on the Lightsail host.
# The dump is downloaded and verified BEFORE anything local is touched,
# so a failed download leaves the local DB exactly as it was.
#
# Steps:
#   1. Find + scp the latest prod backup (fail early, local DB untouched)
#   2. Stop db service, remove the mysql_data volume (also removes any
#      stray econ-dq-mysql container holding the volume)
#   3. Recreate db via docker compose (pinned image, fresh volume)
#   4. Import as root with a temporary memory bump (large imports OOM at
#      the compose mem_limit — see CLAUDE.md gotchas)
#   5. Apply schema migrations from the current checkout (make seed logic)
#
# Overrides: LIGHTSAIL_HOST, LIGHTSAIL_KEY, PYTHON_BIN (e.g. a venv python
# when poetry is not set up in this checkout).

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

LIGHTSAIL_HOST="${LIGHTSAIL_HOST:-ubuntu@18.195.185.188}"
LIGHTSAIL_KEY="${LIGHTSAIL_KEY:-$HOME/.ssh/LightsailDefaultKey-eu-central-1.pem}"
PYTHON_BIN="${PYTHON_BIN:-poetry run python}"
# Pin the compose project so the same containers/volume are targeted no
# matter which checkout (main repo or worktree) this script runs from.
COMPOSE=(docker compose -p econ-newsfeed)
WORK_DIR="/tmp/econ-sync"
DB_NAME="${DB_NAME:-econ_newsfeed}"

if [ ! -f .env ]; then
    echo "ERROR: .env not found in $PROJECT_DIR (need MYSQL_ROOT_PASSWORD)" >&2
    exit 1
fi
MYSQL_ROOT_PASSWORD="$(grep '^MYSQL_ROOT_PASSWORD=' .env | head -1 | cut -d= -f2-)"
if [ -z "$MYSQL_ROOT_PASSWORD" ]; then
    echo "ERROR: MYSQL_ROOT_PASSWORD not set in .env" >&2
    exit 1
fi

# ── 1. Download latest backup (before touching anything local) ──────────────
mkdir -p "$WORK_DIR"
echo "=== Finding latest prod backup on $LIGHTSAIL_HOST ==="
LATEST=$(ssh -i "$LIGHTSAIL_KEY" -o ConnectTimeout=15 "$LIGHTSAIL_HOST" \
    'ls -t /backups/econ_newsfeed_*.sql.gz 2>/dev/null | head -1')
if [ -z "$LATEST" ]; then
    echo "ERROR: no backups found in /backups on the server" >&2
    exit 1
fi
DUMP="$WORK_DIR/$(basename "$LATEST")"
echo "Latest: $LATEST"
if [ -f "$DUMP" ]; then
    echo "Already downloaded: $DUMP"
else
    scp -i "$LIGHTSAIL_KEY" "$LIGHTSAIL_HOST:$LATEST" "$DUMP.partial"
    mv "$DUMP.partial" "$DUMP"
fi
gunzip -t "$DUMP"
# A gzip-valid file can still be a TRUNCATED dump (mysqldump died mid-stream
# but gzip closed cleanly — happened with the 2026-06-01 prod backup, which
# silently lacked researchers/papers). Only a '-- Dump completed' trailer
# proves mysqldump finished.
if ! gunzip -c "$DUMP" | tail -1 | grep -q '^-- Dump completed'; then
    mv "$DUMP" "$DUMP.truncated"
    echo "ERROR: backup is TRUNCATED — no '-- Dump completed' trailer." >&2
    echo "The server-side mysqldump died mid-dump. Fix backups on the server" >&2
    echo "(scripts/backup.sh) and create a fresh one. Local DB left untouched." >&2
    echo "Truncated file kept at $DUMP.truncated for inspection." >&2
    exit 1
fi
echo "Downloaded and verified: $DUMP ($(du -h "$DUMP" | cut -f1))"

# ── 2. Tear down local DB ────────────────────────────────────────────────────
echo "=== Recreating local database volume ==="
if docker ps --format '{{.Names}}' | grep -q '^econ-newsfeed-api'; then
    "${COMPOSE[@]}" stop api
fi
if pgrep -f "uvicorn.*api" >/dev/null 2>&1; then
    echo "WARNING: a local API process appears to be running (make dev?) — stop it to avoid writes during import"
fi
"${COMPOSE[@]}" rm -sf db 2>/dev/null || true
# A repair container (e.g. econ-dq-mysql) may still hold the volume
docker ps -aq --filter volume=econ-newsfeed_mysql_data | xargs -r docker rm -f
docker volume rm -f econ-newsfeed_mysql_data 2>/dev/null || true

# ── 3. Fresh DB under the pinned compose image ──────────────────────────────
"${COMPOSE[@]}" up -d db
DB_CONTAINER=$("${COMPOSE[@]}" ps -q db)
echo -n "Waiting for MySQL to initialize"
for _ in $(seq 1 90); do
    if docker exec "$DB_CONTAINER" mysqladmin ping -uroot -p"$MYSQL_ROOT_PASSWORD" --silent 2>/dev/null; then
        echo " — ready"; break
    fi
    echo -n "."; sleep 2
done
docker exec "$DB_CONTAINER" mysqladmin ping -uroot -p"$MYSQL_ROOT_PASSWORD" --silent 2>/dev/null \
    || { echo "ERROR: MySQL did not become ready" >&2; exit 1; }

# ── 4. Import with temporary memory bump ────────────────────────────────────
echo "=== Importing $(basename "$DUMP") ==="
docker update --memory 1500m --memory-swap 1500m "$DB_CONTAINER" >/dev/null
restore_memory() { docker update --memory 1024m --memory-swap 1024m "$DB_CONTAINER" >/dev/null 2>&1 || true; }
trap restore_memory EXIT
gunzip -c "$DUMP" | docker exec -i "$DB_CONTAINER" mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$DB_NAME"
restore_memory
trap - EXIT
echo "Import complete."

# ── 5. Apply migrations from this checkout ──────────────────────────────────
echo "=== Applying schema migrations ($PYTHON_BIN) ==="
$PYTHON_BIN -c "from database import Database; Database.create_tables(); print('Migrations applied')"

echo
echo "=== Summary ==="
docker exec "$DB_CONTAINER" mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$DB_NAME" -N -e \
    "SELECT CONCAT('researchers: ', COUNT(*)) FROM researchers
     UNION ALL SELECT CONCAT('papers: ', COUNT(*)) FROM papers
     UNION ALL SELECT CONCAT('feed_events: ', COUNT(*)) FROM feed_events
     UNION ALL SELECT CONCAT('latest event: ', COALESCE(MAX(created_at), 'none')) FROM feed_events" 2>/dev/null
echo "Local DB now mirrors: $(basename "$DUMP")"
echo "Dump kept at $DUMP — next run reuses it unless a newer backup exists."
