.PHONY: setup dev seed reset-db scrape

setup:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt
	cd app && npm install

dev:
	.venv/bin/python -m uvicorn api:app --reload --port 8000 & \
	cd app && npm run dev & \
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
