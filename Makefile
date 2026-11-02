.DEFAULT_GOAL := check
.PHONY: install test test-integration lint fmt fmt-check type check

install:
	uv sync --group dev

test:
	uv run pytest -q

test-integration:
	uv run pytest -m integration -q

lint:
	uv run ruff check .

fmt:
	uv run ruff format .

fmt-check:
	uv run ruff format --check .

type:
	uv run mypy src/atlas

check: lint fmt-check type test
