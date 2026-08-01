.PHONY: install test lint run migrate

install:
	python -m pip install -e '.[dev]'

test:
	python -m pytest

lint:
	ruff check backend tests

run:
	uvicorn backend.app.main:app --reload --port 8000

migrate:
	alembic upgrade head

