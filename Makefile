.PHONY: install lint format test validate build docs

install:
	uv sync --all-extras
	cd web && npm ci --legacy-peer-deps

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run pytest

validate:
	uv run statescope validate --strict

build:
	uv run statescope build

docs:
	uv run statescope docs
