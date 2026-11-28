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

# The scraper service (profile "scraper") runs multi-hour fetch passes, so it
# is not restarted with the API. It is rebuilt and restarted only when code it
# runs changed since its image's revision (RESTART_SCRAPER=1 forces it). A
# restart stops it with SIGTERM: in-flight fetches finish, progress is saved
# and the new container resumes the pass (stop_grace_period 120s).
# Keep SCRAPER_PATHS in sync with what backend.pipeline.scrape_worker imports
# (tests/test_scrape_resume.py::TestDeployScraperPaths checks it).
SCRAPER_PATHS=(
    backend/__init__.py backend/config.py backend/researcher.py backend/publication_rules.py
    backend/database
    backend/pipeline/__init__.py backend/pipeline/scheduler.py backend/pipeline/html_fetcher.py
    backend/pipeline/fetch_lanes.py backend/pipeline/scrape_worker.py backend/pipeline/feed_events.py
    backend/enrichment/__init__.py backend/enrichment/paper_merge.py
    backend/enrichment/rename_merge.py backend/enrichment/doi_resolver.py
    backend/llm/__init__.py backend/llm/throttle.py
    Dockerfile.api pyproject.toml poetry.lock docker-compose.yml docker-compose.prod.yml
)
scraper_compose=(docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile scraper)
scraper=$("${scraper_compose[@]}" ps -q scraper 2>/dev/null || true)
if [ -n "$scraper" ]; then
    scraper_revision=$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$scraper" 2>/dev/null || true)
    if [ "${RESTART_SCRAPER:-0}" = 1 ] \
        || ! git cat-file -e "${scraper_revision:-unknown}^{commit}" 2>/dev/null \
        || ! git diff --quiet "$scraper_revision" "$revision" -- "${SCRAPER_PATHS[@]}"; then
        echo "==> Scraper code changed since ${scraper_revision:-unknown}; restarting it (the pass resumes)..."
        "${scraper_compose[@]}" build --build-arg "APP_REVISION=$revision" scraper
        "${scraper_compose[@]}" up -d --no-deps scraper
    else
        echo "==> Scraper unchanged since $scraper_revision; left running"
    fi
fi

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
