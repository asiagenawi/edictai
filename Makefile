.PHONY: install lint format test test-api validate build docs site

install:
	uv sync --all-extras
	cd site && npm ci

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run pytest

test-api:
	uv run --no-project --with-requirements api/requirements-dev.txt python -m pytest api/tests -q

validate:
	uv run statescope validate --strict

build:
	uv run statescope build

docs:
	uv run statescope docs

site: build
	cd site && npm run build
