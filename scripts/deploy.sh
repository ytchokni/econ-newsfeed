#!/usr/bin/env bash
set -euo pipefail

# Deploy econ-newsfeed on Hetzner
# Usage: ./scripts/deploy.sh
#
# Pulls latest code from main, rebuilds and restarts containers.
# Caddy is managed separately (systemd) and does not need restarting.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "==> Pulling latest code..."
git pull --ff-only origin main

echo "==> Building and restarting containers..."
compose=(docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile mining)
revision=$(git rev-parse HEAD)
# Preserve a running database; database configuration changes need their own
# maintenance step. A fresh host still creates and waits for the database.
"${compose[@]}" up -d --no-recreate --wait --wait-timeout 120 db
"${compose[@]}" build --build-arg "APP_REVISION=$revision" api site-miner
"${compose[@]}" up -d --no-deps api site-miner

echo "==> Waiting for API to be ready..."
for i in $(seq 1 150); do
    if curl -sf http://localhost:8000/api/ready > /dev/null 2>&1; then
        for service in api site-miner; do
            container=$("${compose[@]}" ps -q "$service")
            running=$(docker inspect --format '{{.State.Running}}' "$container")
            deployed_revision=$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$container")
            if [ "$running" != true ] || [ "$deployed_revision" != "$revision" ]; then
                echo "ERROR: $service is not running the requested revision" >&2
                exit 1
            fi
        done
        echo "==> API ready; API and miner running $revision"
        exit 0
    fi
    sleep 2
done

echo "ERROR: API did not become healthy within 300 seconds" >&2
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs api --tail 20
exit 1
