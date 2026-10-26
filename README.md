# Atlas Capital

A multi-agent hedge fund simulator: LLM analysts and PMs, with a deterministic quant core for sizing, risk, execution and accounting. Research and paper trading only. This is not investment advice.

## Quick start

1. Install [uv](https://docs.astral.sh/uv/) (it fetches Python 3.12 if needed).
2. `make install` installs the project and dev tools (`uv sync --group dev`).
3. `make check` runs lint, format check, mypy and the test suite.
4. Individual targets: `make test`, `make lint`, `make fmt`, `make type`.
5. Optional git hooks: `uv run pre-commit install`.
6. Before any work, read [`Project.md`](Project.md) (governing rules).
7. Then [`Planning/README.md`](Planning/README.md) for the design, roadmap and stage process.
8. Current status lives in [`Planning/PROGRESS.md`](Planning/PROGRESS.md).
