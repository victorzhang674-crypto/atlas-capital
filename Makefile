.DEFAULT_GOAL := check
.PHONY: install test test-integration lint fmt fmt-check type check

install:
	uv sync --group dev

# -n auto --dist loadfile (pytest-xdist): the test files are spread over the cores and each
# file stays on one worker, so its module fixtures (the synthetic lakes) are built once. The
# wall-clock budgets of the briefs (marker `timing`) then run alone, one after another, so
# other workers do not slow them. A plain `uv run pytest -q` is the same suite, serial.
test:
	uv run pytest -q -n auto --dist loadfile -m 'not live and not llm and not integration and not timing'
	uv run pytest -q -m timing

test-integration:
	uv run pytest -m integration -q -n auto --dist loadfile

lint:
	uv run ruff check .

fmt:
	uv run ruff format .

fmt-check:
	uv run ruff format --check .

type:
	uv run mypy src/atlas

check: lint fmt-check type test
