.PHONY: setup dev seed reset-db

setup:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt
	cd app && npm install
