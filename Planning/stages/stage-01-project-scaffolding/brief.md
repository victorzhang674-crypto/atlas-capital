# Stage 01 — Project scaffolding & toolchain
**Phase:** 0 Foundation · **Size:** S · **Depends on:** — · **Spec refs:** §11, §12

## Goal
Set up a working, empty Python project with the full toolchain, so every later stage only adds code.

## In scope
- `git init`, `.gitignore` (Python, `.env*`, `data/`, `.venv`, `*.duckdb`, `.chroma/`), first commit of the existing `Planning/`
- `pyproject.toml` (uv, Python 3.12, src layout, package `atlas`), dependency groups: `core`, `dev`; leave heavy groups (`quant`, `llm`, `ui`) declared but empty until needed
- Tooling: ruff (lint + format), mypy, pytest (+ pytest-cov, hypothesis), markers `live`, `llm`, `integration` (skipped by default via `addopts`)
- `src/atlas/__init__.py` with `__version__`; empty subpackages from spec §12, each with a one-line docstring
- `tests/conftest.py` with a placeholder fixture; `tests/unit/test_version.py`
- `Makefile` (or `justfile`): `install`, `test`, `lint`, `fmt`, `type`, `check` (= all)
- `CLAUDE.md` at the repo root **already exists** (it imports `@Project.md`). Append a short engineering-conventions digest; keep the `@Project.md` import at the top. Do not modify `Project.md`.
- `README.md` (project) with a 10-line quick start
- `.pre-commit-config.yaml` (ruff, end-of-file, trailing whitespace)

## Out of scope
Any business logic; CI pipelines (a local `make check` is enough).

## Files
`pyproject.toml`, `Makefile`, `CLAUDE.md`, `README.md`, `.gitignore`, `.pre-commit-config.yaml`, `src/atlas/**/__init__.py`, `tests/conftest.py`, `tests/unit/test_version.py`

## Tasks
1. `git init`; write `.gitignore`; commit `Project.md`, `CLAUDE.md` and `Planning/` as "planning: initial plan and governing rules".
2. Write `pyproject.toml`; `uv sync --group dev`.
3. Create the package skeleton from spec §12 (folders + `__init__.py` docstrings).
4. TDD: `test_version.py` asserts `atlas.__version__ == "0.1.0"`.
5. Configure ruff/mypy/pytest in `pyproject.toml`; markers registered; `addopts = "-m 'not live and not llm and not integration'"`.
6. Makefile targets; `make check` passes.
7. Append to `CLAUDE.md`, below the existing `@Project.md` import: conventions summary, stage workflow, "never call `datetime.now()` outside `core.clock`", "data only through DataView", "no real network or LLM in unit tests".

## Tests
- `test_version`; a test that imports every subpackage (catches missing `__init__`).

## Acceptance criteria
- `make check` is green on a clean clone after `uv sync`.
- `git log` shows the planning commit and the stage-01 commit.
- `CLAUDE.md` still begins with the `@Project.md` import; `Project.md` is unchanged.

## Verify
```
uv sync --group dev && make check && git log --oneline
```
