.PHONY: setup dev seed reset-db

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
