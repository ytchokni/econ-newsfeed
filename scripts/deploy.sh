#!/usr/bin/env bash
set -euo pipefail

# Deploy econ-newsfeed on Lightsail
# Usage: ./scripts/deploy.sh
#
# Pulls latest code from main, rebuilds and restarts containers.
# Caddy is managed separately (systemd) and does not need restarting.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "==> Pulling latest code..."
git pull origin main

echo "==> Building and restarting containers..."
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

echo "==> Waiting for API to be ready..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8000/api/health > /dev/null 2>&1; then
        echo "==> API is healthy!"
        exit 0
    fi
    sleep 2
done

echo "ERROR: API did not become healthy within 60 seconds" >&2
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs api --tail 20
exit 1
