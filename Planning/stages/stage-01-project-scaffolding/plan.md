# Stage 01 — Project scaffolding & toolchain · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working, empty Python 3.12 project (`atlas`) with uv, ruff, mypy, pytest and a `make check` that is green on a clean clone, so every later stage only adds code.

**Architecture:** src layout (`src/atlas/`), 17 empty top-level subpackages from spec §12, all tool config in one `pyproject.toml`, a thin `Makefile` wrapping `uv run`. Two git commits: the untouched planning baseline, then `stage-01`.

**Tech Stack:** Python 3.12, uv 0.12, hatchling build backend, ruff, mypy, pytest, pytest-cov, hypothesis, pre-commit, GNU make.

**Spec:** `Planning/stages/stage-01-project-scaffolding/brief.md` (brief) · `Planning/00-design-spec.md` §11, §12 · `Planning/02-stage-protocol.md` §3–§5 · `Project.md` Rules 1–5.

## Global Constraints

- Python `>=3.12,<3.13`; `.python-version` = `3.12`.
- Import package `atlas`; distribution name `atlas-capital`; `atlas.__version__ == "0.1.0"`.
- Markers `live`, `llm`, `integration` registered; `addopts` deselects them: `-m 'not live and not llm and not integration'`.
- mypy is strict on `atlas.core`, `atlas.risk`, `atlas.accounting`, `atlas.execution` (protocol §3).
- `Project.md` must be byte-for-byte unchanged. `CLAUDE.md` keeps its existing header and `@Project.md` import untouched; the digest is appended **below** the `<!-- Stage 01 appends … -->` marker.
- No business logic, no CI pipeline, no config keys, no code from later stages (Rule 2.4 / Rule 4).
- No real network or LLM calls in tests (Rule 5).
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Decisions taken in this plan (log in PROGRESS.md › Decisions log at Task 8)

1. **"core" group = `[project].dependencies`** (runtime deps must live there for the package to install). `dev`, `quant`, `llm`, `ui` are PEP 735 `[dependency-groups]`; `quant`/`llm`/`ui` are declared `[]`. `[project].dependencies` is `[]` in Stage 01 — nothing imports a third-party lib yet.
2. **Only the 17 top-level subpackages** from §12 are created. Nested ones (`data/providers`, `quant/features`, `agents/prompts`, …) are created by the stage that first needs them (YAGNI).
3. **Build backend hatchling** with `packages = ["src/atlas"]` (distribution name ≠ import name).
4. **`CLAUDE.md` "begins with the `@Project.md` import"** is read as: the existing first 5 lines (title, sentence, import) stay identical and precede the digest. The file did not literally start with the import at planning time; no change to that header.
5. **pre-commit whitespace hooks exclude `^(Project\.md|Planning/)`** so `pre-commit run --all-files` can never rewrite the governing rules or plan. Hooks are not installed into `.git/hooks` this stage (installing would make every commit depend on fetching github repos).
6. `uv.lock` is committed (needed for "green on a clean clone").

## File map

| Path | Responsibility |
|---|---|
| `.gitignore` | Python/tool caches, `.venv/`, `.env*` (except `.env.example`), `data/`, `*.duckdb*`, `.chroma/`, `.DS_Store`, `.claude/settings.local.json`, `**/.claude/.cc-writes/` |
| `.python-version` | `3.12` |
| `pyproject.toml` | metadata, deps, build, ruff/mypy/pytest config |
| `uv.lock` | generated |
| `src/atlas/__init__.py` | package docstring + `__version__` |
| `src/atlas/<pkg>/__init__.py` ×17 | one-line docstring each |
| `tests/conftest.py` | `repo_root` fixture |
| `tests/unit/test_version.py` | version tests |
| `tests/unit/test_package_layout.py` | every subpackage imports, has a docstring, no stray dirs |
| `tests/unit/test_toolchain.py` | pytest marker/addopts safety rail |
| `tests/unit/test_repo_docs.py` | CLAUDE.md import order; pre-commit exclude |
| `Makefile` | `install test lint fmt fmt-check type check` |
| `.pre-commit-config.yaml` | ruff, ruff-format, end-of-file-fixer, trailing-whitespace |
| `CLAUDE.md` | append conventions digest |
| `README.md` | 10-line quick start |
| `Planning/PROGRESS.md` | status, decisions, handoff, evidence |

## Review Focus

1. **Default test run hits live services** — a `live`/`llm`/`integration` test must be deselected by plain `pytest`, and a misspelled marker must error, not silently run. → `test_toolchain.py` (Task 5).
2. **Subpackage silently missing `__init__.py`** (namespace package imports fine but breaks tooling) or a stray directory appears. → `test_package_layout.py` filesystem + `pkgutil` checks (Task 4).
3. **Version drift** between `pyproject.toml` and `atlas.__version__`. → `test_version_matches_pyproject` (Task 3).
4. **Secrets committed** — `.env`, `.env.local` must be ignored while `.env.example` stays committable. → `git check-ignore` step (Task 1).
5. **Tooling rewrites governing docs** — pre-commit whitespace fixers touching `Project.md`/`Planning/`. → `test_precommit_excludes_governing_docs` (Task 7), plus `git diff` check on `Project.md` (Task 8).

---

### Task 0: Gate check and stage start

**Files:** Modify `Planning/PROGRESS.md`

- [ ] **Step 1:** Confirm Stage 00 is `DONE` in `PROGRESS.md` (handoff note dated 2026-10-25 present). `uv run pytest -q` is not applicable before Stage 01 (Rule 4.4 "from Stage 01 onward") — note this in the handoff.
- [ ] **Step 2:** Record a SHA-256 of `Project.md`: `shasum -a 256 Project.md` → keep the hash for Task 8.
- [ ] **Step 3:** In `PROGRESS.md` set `Current stage: 01 — Project scaffolding & toolchain — IN PROGRESS`, `Last updated: <today>`.

### Task 1: git init, .gitignore, planning commit

**Files:** Create `.gitignore`

- [ ] **Step 1:** `git init -b main` in the project root.
- [ ] **Step 2:** Write `.gitignore` with the entries in the file map (plus standard Python: `__pycache__/`, `*.py[cod]`, `*.egg-info/`, `build/`, `dist/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.coverage`, `htmlcov/`, `.hypothesis/`).
- [ ] **Step 3: Verify ignore rules.**
  Run: `git check-ignore -v .env .env.local data/raw/x.parquet .venv/x foo.duckdb .chroma/x Planning/.DS_Store`
  Expected: every path printed with a matching rule.
  Run: `git check-ignore .env.example; echo $?` → Expected: `1` (not ignored).
- [ ] **Step 4:** `git add Project.md CLAUDE.md Planning/` then `git status --short` → only those paths staged, no `.DS_Store`, no `.claude/`.
- [ ] **Step 5: Commit** `planning: initial plan and governing rules` (+ trailer). `git log --oneline` → 1 commit.

### Task 2: pyproject.toml and environment

**Files:** Create `pyproject.toml`, `.python-version`, `src/atlas/__init__.py` (docstring only); generated `uv.lock`

- [ ] **Step 1:** Write `pyproject.toml`:
  - `[project]` name `atlas-capital`, version `"0.1.0"`, description, `requires-python = ">=3.12,<3.13"`, `dependencies = []`.
  - `[build-system]` hatchling; `[tool.hatch.build.targets.wheel] packages = ["src/atlas"]`.
  - `[dependency-groups]` `dev = ["ruff>=0.6", "mypy>=1.11", "pytest>=8.3", "pytest-cov>=5", "hypothesis>=6.100", "pre-commit>=3.8"]`, `quant = []`, `llm = []`, `ui = []`.
  - No `[tool.*]` sections yet (Task 5).
- [ ] **Step 2:** `.python-version` = `3.12`; `src/atlas/__init__.py` = `"""Atlas Capital — multi-agent hedge fund simulator (paper trading only)."""` (no `__version__` yet — Task 3 adds it test-first).
- [ ] **Step 3: Sync.** Run: `uv sync --group dev` → Expected: resolves, creates `.venv`, writes `uv.lock`, installs `atlas-capital` editable.
- [ ] **Step 4: Verify.** `uv run python -c "import sys, atlas; print(sys.version_info[:2])"` → `(3, 12)`; `uv run ruff --version`, `uv run mypy --version`, `uv run pytest --version` all print versions.

### Task 3: atlas.__version__ (TDD)

**Files:** Create `tests/unit/test_version.py`; Modify `src/atlas/__init__.py`

**Interfaces — Produces:** `atlas.__version__: str` = `"0.1.0"`.

- [ ] **Step 1: Write the failing tests**
```python
def test_version() -> None:
    assert atlas.__version__ == "0.1.0"

def test_version_matches_pyproject(repo_root: Path) -> None:
    meta = tomllib.loads((repo_root / "pyproject.toml").read_text())
    assert meta["project"]["version"] == atlas.__version__
```
  (`repo_root` fixture is created in this task: `tests/conftest.py`, `def repo_root() -> Path` returning `Path(__file__).resolve().parents[1]`, session-scoped, docstring "Absolute path of the repository root.")
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_version.py -q` → FAIL: `AttributeError: module 'atlas' has no attribute '__version__'`.
- [ ] **Step 3:** Add `__version__ = "0.1.0"` and `__all__ = ["__version__"]` to `src/atlas/__init__.py`.
- [ ] **Step 4: Run** same command → 2 passed.

### Task 4: Subpackage skeleton (TDD)

**Files:** Create `tests/unit/test_package_layout.py`; 17 × `src/atlas/<pkg>/__init__.py`

**Interfaces — Produces:** importable `atlas.core`, `atlas.data`, `atlas.quant`, `atlas.llm`, `atlas.agents`, `atlas.portfolio`, `atlas.risk`, `atlas.compliance`, `atlas.execution`, `atlas.accounting`, `atlas.performance`, `atlas.orchestration`, `atlas.backtest`, `atlas.live`, `atlas.reporting`, `atlas.dashboard`, `atlas.cli`.

- [ ] **Step 1: Write the failing tests** with `SUBPACKAGES` = the 17 names above:
  - `test_subpackage_imports[<name>]` (parametrized): `importlib.import_module(f"atlas.{name}")`; its `__doc__` is non-empty, stripped, and has no newline (one line).
  - `test_no_unexpected_subpackages`: `{m.name for m in pkgutil.iter_modules(atlas.__path__) if m.ispkg} == set(SUBPACKAGES)`.
  - `test_every_source_dir_has_init(repo_root)`: every directory under `src/atlas` (excluding `__pycache__`) contains `__init__.py`.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_package_layout.py -q` → FAIL (`ModuleNotFoundError: No module named 'atlas.core'`, set mismatch).
- [ ] **Step 3:** Create the 17 `__init__.py` files, each a single docstring line taken from spec §12, e.g. `atlas.core` → `"""Core infrastructure: config, logging, audit, clock, calendar, types, storage, errors."""`; `atlas.risk` → `"""Risk engine: hard limits, monitor, circuit breakers, stress tests."""`. No other code.
- [ ] **Step 4: Run** same command → 19 passed. Then `uv run pytest -q` → 21 passed.

### Task 5: ruff / mypy / pytest configuration (TDD for the safety rail)

**Files:** Create `tests/unit/test_toolchain.py`; Modify `pyproject.toml`

- [ ] **Step 1: Write the failing tests** (read `pyproject.toml` via `tomllib` + `repo_root`):
  - `test_markers_registered`: `[tool.pytest.ini_options].markers` contains entries starting `live:`, `llm:`, `integration:`.
  - `test_default_run_deselects_external_markers`: `addopts` contains `-m 'not live and not llm and not integration'` and `--strict-markers`.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_toolchain.py -q` → FAIL (`KeyError: 'tool'`).
- [ ] **Step 3:** Add to `pyproject.toml`:
  - `[tool.pytest.ini_options]` `minversion = "8.0"`, `testpaths = ["tests"]`, `addopts = "-ra --strict-markers -m 'not live and not llm and not integration'"`, markers `live: hits real network services`, `llm: calls a real LLM API`, `integration: slower multi-component tests in tests/integration`.
  - `[tool.ruff]` `line-length = 100`, `target-version = "py312"`, `src = ["src", "tests"]`; `[tool.ruff.lint] select = ["E", "W", "F", "I", "B", "UP", "SIM", "RUF"]`.
  - `[tool.mypy]` `python_version = "3.12"`, `files = ["src/atlas"]`, `warn_unused_configs`, `warn_redundant_casts`, `warn_unused_ignores`, `check_untyped_defs = true`; `[[tool.mypy.overrides]]` for `module = ["atlas.core.*", "atlas.risk.*", "atlas.accounting.*", "atlas.execution.*"]` with the strict flag set: `disallow_untyped_defs`, `disallow_incomplete_defs`, `disallow_untyped_calls`, `disallow_untyped_decorators`, `disallow_any_generics`, `warn_return_any`, `no_implicit_reexport`, `strict_equality` = true.
- [ ] **Step 4: Run** `uv run pytest tests/unit/test_toolchain.py -q` → 2 passed.
- [ ] **Step 5: Behavioural check of the rail** (scratch file in `$TMPDIR`, not committed): a test file with one `@pytest.mark.live` test → `uv run pytest -q <file> --rootdir . -c pyproject.toml` → `1 deselected`; a test with `@pytest.mark.lvie` → error `'lvie' not found in markers`.
- [ ] **Step 6:** `uv run ruff check . && uv run ruff format --check . && uv run mypy src/atlas` → all clean (fix and re-run if not).

### Task 6: Makefile and pre-commit config

**Files:** Create `Makefile`, `.pre-commit-config.yaml`

- [ ] **Step 1:** `Makefile` (recipes tab-indented, all `.PHONY`): `install: uv sync --group dev` · `test: uv run pytest -q` · `lint: uv run ruff check .` · `fmt: uv run ruff format .` · `fmt-check: uv run ruff format --check .` · `type: uv run mypy src/atlas` · `check: lint fmt-check type test`. Default goal `check`.
- [ ] **Step 2:** `.pre-commit-config.yaml`: `astral-sh/ruff-pre-commit` (hooks `ruff` with `args: [--fix]`, `ruff-format`; `rev` = `v` + the version printed by `uv run ruff --version`); `pre-commit/pre-commit-hooks` `rev: v5.0.0` (hooks `end-of-file-fixer`, `trailing-whitespace`, both with `exclude: ^(Project\.md|Planning/)`).
- [ ] **Step 3: Verify.** `make -n check` prints the four commands (proves tabs/targets parse). `uv run pre-commit validate-config .pre-commit-config.yaml` → exit 0. `make check` → lint, format, mypy clean; 23 passed.

### Task 7: CLAUDE.md digest and README (TDD for the doc invariants)

**Files:** Create `tests/unit/test_repo_docs.py`, `README.md`; Modify `CLAUDE.md` (append only)

- [ ] **Step 1: Write the tests**
  - `test_claude_md_imports_project_rules_first(repo_root)`: in `CLAUDE.md`, the line `@Project.md` exists, and its index is lower than the index of the line `## Engineering conventions (digest)`.
  - `test_precommit_excludes_governing_docs(repo_root)`: `.pre-commit-config.yaml` text contains `exclude: ^(Project\.md|Planning/)` at least twice.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_repo_docs.py -q` → `test_claude_md_…` FAILS (heading not present yet); pre-commit test passes (Task 6 already wrote it — it pins the decision going forward).
- [ ] **Step 3:** Append under the marker comment in `CLAUDE.md` (≤30 lines): `## Engineering conventions (digest)` — layout/tests mirror, Pydantic v2 vs frozen dataclass, weights as fractions of NAV (short < 0), simple daily returns unless `log_`, UTC-aware datetimes, `Decimal` for ledger/cash/fees, every stochastic fn takes `seed`/`rng`, config in YAML via `AtlasConfig`, custom errors in `atlas.core.errors`, structlog with `run_id`; **hard rules:** never call `datetime.now()` outside `atlas.core.clock`; data only through `DataView`; no real network or LLM in unit tests (`live`/`llm` markers); paper trading only. `## Stage workflow` — gate check → plan in `Planning/stage-plans/stage-NN-plan.md` → TDD task by task → `make check` + brief's Verify → evidence block in `PROGRESS.md` → `DONE` → commit `stage-NN: <title>`. Refer to `02-stage-protocol.md` for the full tables.
- [ ] **Step 4:** Write `README.md` (title, one-line description + "not investment advice / paper only", then ≤10 quick-start lines: install uv + Python 3.12 → `uv sync --group dev` / `make install` → `make check` → individual targets → read `Project.md` then `Planning/README.md`).
- [ ] **Step 5: Run** `uv run pytest -q` → 25 passed. `git diff -- CLAUDE.md` → only `+` lines after the marker line; `git diff --quiet -- Project.md` → exit 0.

### Task 8: Full verification, evidence, commit

**Files:** Modify `Planning/PROGRESS.md`

- [ ] **Step 1: Full suite in the working tree** (Rule 1.1): `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src/atlas` — read each output; all green.
- [ ] **Step 2:** `shasum -a 256 Project.md` equals the Task 0 hash.
- [ ] **Step 3:** Write the Stage 01 handoff note in `PROGRESS.md` (template §6: built, key interfaces = `atlas.__version__`, 17 subpackages, `repo_root` fixture, make targets; Decisions 1–6 above also added to the Decisions log; collisions: none; deviations: none or what happened; next stage: Stage 02 adds `[project].dependencies` entries for pydantic/pyyaml and `config/`). Status stays `IN PROGRESS`.
- [ ] **Step 4: Commit** `git add -A` → review `git status --short` (no `.venv`, `.DS_Store`, caches) → commit `stage-01: Project scaffolding & toolchain` (+ trailer).
- [ ] **Step 5: Clean-clone verify (the brief's Verify).**
  Run: `git clone "<repo>" "$TMPDIR/atlas-clone" && cd "$TMPDIR/atlas-clone" && uv sync --group dev && make check && git log --oneline`
  Expected: lint/format/mypy clean, `25 passed`, log shows `stage-01: …` and `planning: …`.
- [ ] **Step 6:** Write the **Verification evidence — Stage 01** block (Project.md §1.5) with the real output lines from Steps 1, 2, 5, and each acceptance criterion:
  - `make check` green on clean clone after `uv sync` — evidence: Step 5 output.
  - `git log` shows planning + stage-01 commits — evidence: Step 5 `git log --oneline`.
  - `CLAUDE.md` keeps `@Project.md` import at top; `Project.md` unchanged — evidence: `test_claude_md_imports_project_rules_first`, hash match, `git diff HEAD~1 HEAD -- Project.md` empty.
  Then tick Stage 01, set it `DONE`, `Stages done: 1 / 66`, current stage → `02 — Configuration system & fund mandate — NOT STARTED`.
- [ ] **Step 7:** `git add Planning/PROGRESS.md && git commit --amend --no-edit` (keeps the single `stage-01` commit; local, never pushed). `git log --oneline` → exactly 2 commits.
