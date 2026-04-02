.PHONY: setup dev kill seed reset-db scrape fetch parse parse-fast batch-submit batch-check classify-jel enrich enrich-jel discover-domains backfill-normalize populate-fields check

setup:
	poetry install
	cd app && npm install

dev:
	@trap 'kill 0' INT TERM; \
	poetry run uvicorn api:app --reload --port 8001 & \
	cd app && API_INTERNAL_URL=http://localhost:8001 npm run dev & \
	wait

kill:
	@lsof -ti :8000 -ti :8001 -ti :3000 -ti :3001 2>/dev/null | xargs kill -9 2>/dev/null || true
	@echo "Killed processes on ports 8000, 8001, 3000, and 3001"

seed:
	poetry run python -c "from database import Database; Database.create_database(); Database.create_tables(); print('Database seeded')"

reset-db:
	poetry run python -c "from database import Database; from db_config import db_config; \
		import mysql.connector; \
		conn = mysql.connector.connect(host=db_config['host'], user=db_config['user'], password=db_config['password']); \
		cursor = conn.cursor(); \
		cursor.execute('DROP DATABASE IF EXISTS \`' + db_config['database'] + '\`'); \
		conn.close(); \
		Database.create_database(); \
		Database.create_tables(); \
		print('Database reset complete')"

scrape:
	poetry run python -c "from scheduler import run_scrape_job; run_scrape_job()"

fetch:
	poetry run python -c "from main import download_htmls; download_htmls()"

parse:
	poetry run python -c "from main import extract_data_from_htmls; extract_data_from_htmls()"

parse-fast:
	poetry run python -c "from main import extract_data_from_htmls_concurrent; extract_data_from_htmls_concurrent()"

batch-submit:
	poetry run python -c "from main import batch_submit; batch_submit()"

batch-check:
	poetry run python -c "from main import batch_check; batch_check()"

classify-jel:
	poetry run python -c "from main import classify_jel; classify_jel()"

enrich:
	poetry run python main.py enrich

enrich-jel:  ## Enrich researcher JEL codes from paper topics
	poetry run python main.py enrich-jel

discover-domains:  ## Scan for untrusted domains that may host paper links
	poetry run python main.py discover-domains

backfill-normalize:  ## Re-normalize html_content hashes (one-time, after deploying text normalization)
	poetry run python scripts/backfill_normalized_hashes.py

populate-fields:  ## Backfill researcher_fields from JEL codes (one-time)
	poetry run python scripts/backfill_researcher_fields.py

check:
	@echo "=== Step 1: Env validation ==="
	poetry run python scripts/check_env.py
	@echo "=== Step 2: Python tests ==="
	poetry run pytest
	@echo "=== Step 3: TypeScript check ==="
	cd app && npx tsc --noEmit
	@echo "=== Step 4: Frontend tests ==="
	cd app && npx jest
	@echo "=== All checks passed ==="
