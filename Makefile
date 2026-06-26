.PHONY: setup dev kill seed reset-db scrape fetch extract batch-submit batch-check classify-jel enrich enrich-jel discover-domains discover-urls discover-research-pages backfill-normalize populate-fields backfill-affiliations audit-zero-pubs ingest-newsletters check

setup:
	poetry install
	cd app && npm install

dev:
	@trap 'kill 0' INT TERM; \
	poetry run uvicorn backend.api:app --reload --port 8001 & \
	cd app && API_INTERNAL_URL=http://localhost:8001 npm run dev & \
	wait

kill:
	@lsof -ti :8000 -ti :8001 -ti :3000 -ti :3001 2>/dev/null | xargs kill -9 2>/dev/null || true
	@echo "Killed processes on ports 8000, 8001, 3000, and 3001"

seed:
	poetry run python -c "from backend.database import create_database, create_tables; create_database(); create_tables(); print('Database seeded')"

reset-db:
	poetry run python -c "from backend.database import create_database, create_tables; from backend.config import db_config; \
		import mysql.connector; \
		conn = mysql.connector.connect(host=db_config['host'], user=db_config['user'], password=db_config['password']); \
		cursor = conn.cursor(); \
		cursor.execute('DROP DATABASE IF EXISTS \`' + db_config['database'] + '\`'); \
		conn.close(); \
		create_database(); \
		create_tables(); \
		print('Database reset complete')"

scrape:
	poetry run python -c "from backend.pipeline.scheduler import run_scrape_job; run_scrape_job()"

fetch:
	poetry run python -c "from backend.main import download_htmls; download_htmls()"

extract:  ## Run LLM extraction for URLs with pending changes (no fetching)
	poetry run python -m backend.main extract

batch-submit:
	poetry run python -m backend.main batch-submit

batch-check:
	poetry run python -m backend.main batch-check

classify-jel:
	poetry run python -c "from backend.main import classify_jel; classify_jel()"

enrich:
	poetry run python -m backend.main enrich

enrich-jel:  ## Enrich researcher JEL codes from paper topics
	poetry run python -m backend.main enrich-jel

discover-domains:  ## Scan for untrusted domains that may host paper links
	poetry run python -m backend.main discover-domains

discover-urls:  ## Find personal websites for researchers via Google search
	poetry run python -m backend.main discover-urls

discover-research-pages:  ## Find research/publications pages from stored homepage HTML
	poetry run python scripts/discover_research_pages.py

backfill-normalize:  ## Re-normalize html_content hashes (one-time, after deploying text normalization)
	poetry run python scripts/backfill_normalized_hashes.py

populate-fields:  ## Backfill researcher_fields from JEL codes (one-time)
	poetry run python scripts/backfill_researcher_fields.py

backfill-affiliations:  ## Backfill researcher affiliations from OpenAlex
	poetry run python scripts/backfill_affiliations.py

audit-zero-pubs:  ## Audit researchers with URLs but 0 publications
	poetry run python scripts/audit_zero_pub_researchers.py

ingest-newsletters:  ## Ingest papers from newsletter emails (Gmail inbox)
	poetry run python -m backend.main ingest-newsletters

backfill-page-owner:  ## Backfill page owner as author on papers from their page
	poetry run python scripts/backfill_page_owner_authorship.py

check:
	@echo "=== Step 1: Env validation ==="
	poetry run python scripts/check_env.py
	@echo "=== Step 2: Python tests ==="
	poetry run pytest
	@echo "=== Step 3: TypeScript check ==="
	cd app && npx tsc --noEmit
	@echo "=== Step 4: ESLint ==="
	cd app && npx next lint
	@echo "=== Step 5: Frontend tests ==="
	cd app && npx jest
	@echo "=== All checks passed ==="

check-data:  ## Data-quality invariant checks against the real DB (.env); fails on bad rows
	poetry run pytest tests_data_quality -v

check-data-live:  ## check-data + pipeline-liveness checks (run on the server against the live DB)
	DATA_QUALITY_LIVE=1 poetry run pytest tests_data_quality -v

sync-prod:  ## DESTRUCTIVE: replace local DB with latest prod backup (scp + fresh volume + migrations)
	./scripts/sync_prod_db.sh

# Eval
eval-export:
	poetry run python eval/export_test_cases.py

eval-run:
	@for f in eval/configs/*.yaml; do \
		echo "=== Running $$(basename $$f .yaml) ===" && \
		OPENROUTER_API_KEY=$$(grep OPEN_ROUTER_API_KEY .env | cut -d= -f2) npx promptfoo@latest eval -c "$$f"; \
	done

eval-view:
	cd eval && npx promptfoo@latest view

eval: eval-export eval-run eval-view
