# Atlas Capital — instructions for the AI

The governing rules below are imported automatically. Follow them before and during **every** task.

@Project.md

## Where things are
- Overall plan: `Planning/01-master-roadmap.md` (design annex: `Planning/00-design-spec.md`)
- Process: `Planning/02-stage-protocol.md`
- Current status and handoff notes: `Planning/PROGRESS.md`
- Stage folders: `Planning/stages/stage-NN-<slug>/` (`brief.md`, `plan.md`, `worklog.md`, `verification.md`, `artifacts/`); all stage work is recorded there (Project.md Rule 2A). Template: `Planning/stages/_template/`

<!-- Stage 01 appends the engineering-conventions digest below this line. Keep the @Project.md import at the top. -->

## Engineering conventions (digest)
Full tables: `Planning/02-stage-protocol.md` §4–§5.

- **Commands:** `make install` (= `uv sync --group dev`), `make check` (= lint + format check + mypy + tests). Also `make test`, `lint`, `fmt`, `type`.
- **Layout:** code in `src/atlas/<package>/`; tests mirror it in `tests/unit/<package>/`; integration tests in `tests/integration/` marked `@pytest.mark.integration`.
- **Types:** Pydantic v2 for contracts that cross packages; `@dataclass(frozen=True)` for internal values. mypy is strict on `core`, `risk`, `accounting`, `execution`.
- **Units:** weights are fractions of NAV (0.05 = 5%, shorts negative); returns are simple daily unless named `log_`; `Decimal` for ledger, cash and fees; timezone-aware UTC datetimes, `date` for trading days. Docstrings state units.
- **Config and errors:** nothing hard-coded; limits and thresholds live in `config/*.yaml`, read through `AtlasConfig`. Custom exceptions in `atlas.core.errors`; never swallow an exception; failing safe means no new risk.
- **Randomness and logging:** every stochastic function takes `seed`/`rng`; log with structlog via `atlas.core.logging.get_logger`, carrying the `run_id`.

### Hard rules
- Never call `datetime.now()` outside `atlas.core.clock`.
- Strategy and agent code gets data only through `DataView` (point-in-time).
- No real network or LLM calls in unit tests: use fixtures and fakes; live tests are marked `live` / `llm` and deselected by default.
- Paper trading only; never add a live-money code path. Never commit secrets or print API keys.

## Stage workflow
1. Read `Project.md`; run the Rule 4 gate check (previous stage `DONE`, `uv run pytest -q` green).
2. Copy `Planning/stages/_template/` into the stage folder; write `plan.md` from `brief.md`.
3. Execute tasks in order, test-first (red → green → refactor); log work in `worklog.md`.
4. Run `make check` plus the brief's Verify commands; record real output in `verification.md`.
5. Only then tick the stage `DONE` in `PROGRESS.md` and commit `stage-NN: <title>`.
6. Do every git step yourself and verify it (Project.md Rule 6A): review the staged diff, commit, check `git status` / `git log`, merge into `main`, re-run `uv run pytest -q` and `make check` on the merged result, and record the hashes. Never ask the owner to run git.
