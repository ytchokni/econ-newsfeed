.PHONY: setup dev seed reset-db scrape fetch parse parse-fast batch-submit batch-check

setup:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt
	cd app && npm install

dev:
	.venv/bin/python -m uvicorn api:app --reload --port 8001 & \
	cd app && API_INTERNAL_URL=http://localhost:8001 npm run dev & \
	wait

seed:
	.venv/bin/python database.py

reset-db:
	.venv/bin/python -c "from database import Database; from db_config import db_config; \
		import mysql.connector; \
		conn = mysql.connector.connect(host=db_config['host'], user=db_config['user'], password=db_config['password']); \
		cursor = conn.cursor(); \
		cursor.execute('DROP DATABASE IF EXISTS `' + db_config['database'] + '`'); \
		conn.close(); \
		Database.create_database(); \
		Database.create_tables(); \
		print('Database reset complete')"

scrape:
	.venv/bin/python -c "from scheduler import run_scrape_job; run_scrape_job()"

fetch:
	.venv/bin/python -c "from main import download_htmls; download_htmls()"

parse:
	.venv/bin/python -c "from main import extract_data_from_htmls; extract_data_from_htmls()"

parse-fast:
	.venv/bin/python -c "from main import extract_data_from_htmls_concurrent; extract_data_from_htmls_concurrent()"

batch-submit:
	.venv/bin/python -c "from main import batch_submit; batch_submit()"

batch-check:
	.venv/bin/python -c "from main import batch_check; batch_check()"
