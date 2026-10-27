# Stage Protocol — how every chat works

Every stage is built in **one fresh chat**. This file is the contract each chat follows, so that a chat with no memory of earlier ones can pick up cleanly.

> [`Project.md`](../Project.md) governs everything here: read-first, verify before claiming done, the overall plan wins collisions, and strict stage order. If this file and `Project.md` disagree, `Project.md` wins.

## 1. Kick-off prompt (paste this to start a stage)

```
We are building Atlas Capital, a multi-agent hedge fund simulator.
Start Stage NN.

0. Read Project.md in full first, then state the current stage and its status.
   Run the Rule 4 gate check: Stage NN-1 must be DONE with a Verification evidence block in
   Planning/PROGRESS.md and in its folder's verification.md, and `uv run pytest -q` must be green. If either check fails, stop and
   report; do not begin Stage NN.
1. Read Planning/README.md, Planning/01-master-roadmap.md (the overall plan),
   Planning/00-design-spec.md (skim; read the sections the stage cites),
   Planning/02-stage-protocol.md, Planning/PROGRESS.md and Planning/stages/stage-NN-<slug>/brief.md.
2. Read CLAUDE.md and the "Handoff notes" of the previous stage in PROGRESS.md.
3. Use the Superpowers writing-plans skill to expand the stage brief into a bite-sized TDD plan
   saved in the stage folder (copy Planning/stages/_template/ into Planning/stages/stage-NN-<slug>/ first),
   then execute it task by task (executing-plans / TDD), logging each step in worklog.md.
   Record all stage work in that folder (Project.md Rule 2A); code and tests go in src/atlas/ and tests/.
   If anything collides with the overall plan, follow the overall plan and log it (Project.md Rule 3).
4. Finish with the Definition of Done in 02-stage-protocol.md. Claim DONE only after running the
   full verification in this chat and recording the evidence (Project.md Rule 1).
```

> If the Superpowers plugin is not enabled, follow the same steps by hand: write the plan file first, then do red → green → refactor for each task.

## 2. Inside the chat

1. **Gate check (≤10 min, required).** Read `Project.md`. Confirm the previous stage is `DONE` in `PROGRESS.md` and that its folder's `verification.md` says **ALL CRITERIA VERIFIED**, and run `uv run pytest -q` to confirm the baseline is green. If either check fails, **do not start this stage**: report it, and work only on bringing the previous stage to `DONE` (`Project.md` Rule 4). Then set this stage to `IN PROGRESS` in `PROGRESS.md`.
2. **Set up the stage folder and plan.** Copy `Planning/stages/_template/` (`plan.md`, `worklog.md`, `verification.md`, `artifacts/`) into `Planning/stages/stage-NN-<slug>/`, next to `brief.md`. Turn the brief's *Tasks* into 2–5-minute steps, each with exact file paths, the test to write, the command to run and the expected result, and save them in that folder's `plan.md`. From here on, every work record of the stage (log entries, outputs, reports) goes in this folder; only code and tests go in `src/atlas/` and `tests/` (`Project.md` Rule 2A).
3. **Build test-first, in plan order.** For each task: write a failing test → run it and see it fail → write the minimal code → see it pass → refactor → run the module's tests. A task is complete only once its tests have been run and passed in this chat.
4. **Stay in scope.** Anything outside the stage's *In scope* list goes into `PROGRESS.md › Backlog`, not into the code. Never build any part of a later stage early.
5. **Resolve collisions with the overall plan.** If the brief, the stage plan or the code conflicts with `01-master-roadmap.md` or `00-design-spec.md`, follow the overall plan and log the collision in the Decisions log. Do not edit the overall plan without the owner's approval.
6. **Use no real network calls in unit tests.** Use recorded fixtures under `tests/fixtures/` (VCR-style JSON/Parquet) or fakes. Live-network tests are marked `@pytest.mark.live` and skipped by default.
7. **Use no real LLM calls in unit tests.** Use `FakeLLM` (from Stage 24) with scripted responses. Live-LLM tests are marked `@pytest.mark.llm`.
8. **Run every command yourself.** Installs, tests, checks, data syncs, backfills, servers and visual checks are all run and inspected by the AI. Long jobs run in the background. The owner is never asked to run or check something unless a `Project.md` Rule 6 exception applies, and then the AI states the exception, gives the exact command, and keeps the item `BLOCKED` until it sees real output.

## 3. Definition of Done (every stage)

A stage is `DONE` only when **every** box below is ticked from checks **run in this chat** (`Project.md` Rule 1). Otherwise its status stays `IN PROGRESS` or `BLOCKED`.

- [ ] Every acceptance criterion in the stage brief is met, each with named evidence (a test, a command output, or a file).
- [ ] The stage brief's `## Verify` commands were run **by the AI** in this chat and their output read (`Project.md` Rule 6).
- [ ] `uv run pytest -q` is green, with no tests skipped beyond the `live`/`llm` markers.
- [ ] `uv run ruff check . && uv run ruff format --check .` are clean.
- [ ] `uv run mypy src/atlas` is clean (strict for `core`, `risk`, `accounting`, `execution`).
- [ ] New public functions and classes have docstrings that state units (for example "weights as a fraction of NAV", "returns are simple, daily").
- [ ] Every new config key is in `config/*.yaml` with a comment and in `atlas.core.config` with a default.
- [ ] The stage folder holds `plan.md`, a complete `worklog.md`, and raw verification output in `artifacts/`.
- [ ] A **Verification evidence** block (format in `Project.md` §1.5) is written in the stage folder's `verification.md` and summarised in `PROGRESS.md` with a link, *then* the stage is ticked and set to `DONE`, along with the date, key files, decisions made, backlog items and **Handoff notes** for the next stage.
- [ ] One git commit: `stage-NN: <title>` (plus the attribution trailer if the assistant adds one).

## 4. Engineering conventions

| Topic | Rule |
|---|---|
| Layout | `src/atlas/<package>/`; tests mirror it under `tests/unit/<package>/` |
| Types | Pydantic v2 models for contracts crossing packages; `@dataclass(frozen=True)` for internal values |
| Money | `float` for analytics; **`Decimal`** for the ledger, cash and fees (Stage 52) |
| Weights | Fractions of NAV (0.05 = 5%); short positions are negative |
| Returns | Simple daily returns unless the name says `log_` |
| Time | Timezone-aware UTC `datetime`s internally; `date` for trading days; never call `datetime.now()` outside `atlas.core.clock` |
| Data access | Only through `DataView` (from Stage 13). Code that reads the lake directly from strategy or agent code fails review |
| Randomness | Every stochastic function takes `seed` / `rng` |
| Errors | Custom exceptions in `atlas.core.errors`; never swallow an exception silently; fail safe means no new risk |
| Logging | `structlog` through `atlas.core.logging.get_logger`; carry the `run_id` |
| Config | Nothing hard-coded: limits, windows and thresholds live in YAML and are read through `AtlasConfig` |
| Prompts | Jinja2 templates in `src/atlas/agents/prompts/`, versioned by a header comment `{# version: N #}` |
| LLM outputs | Always Pydantic-validated; on a validation failure retry once with the error shown, then fail safe |
| Performance | Vectorise with pandas/numpy; nested Python loops over tickers×dates are not allowed |

## 5. Test conventions

- Unit tests are fast (the whole suite runs in under 60 s until Phase 8).
- Shared fixtures go in `tests/conftest.py`: `tiny_universe` (5 tickers, 300 days of synthetic prices with known properties), `fake_llm`, `sim_clock`, `tmp_lake`.
- Use synthetic data with **known answers** for maths (for example a GBM with a known volatility, so the estimated vol lands within tolerance).
- **Look-ahead tests:** every data path has a test that asks for data after `as_of` and asserts `LookAheadError`.
- Use property-based tests (hypothesis) for accounting invariants (for example, cash plus market value equals NAV).
- Integration tests live in `tests/integration/` and may take longer; mark them `@pytest.mark.integration`.

## 6. Handoff note template (append to PROGRESS.md)

```
### Stage NN — <title> — DONE YYYY-MM-DD
- Built: <modules / CLI commands>
- Key interfaces: <signatures later stages rely on>
- Decisions: <choices made, with reasons>
- Collisions with the overall plan: <none | what collided and how it was resolved>
- Deviations from brief: <none | what and why>
- Known gaps → backlog: <items>
- Next stage should know: <gotchas>

- Stage folder: [stages/stage-NN-<slug>/](stages/stage-NN-<slug>/)

#### Verification evidence — Stage NN — YYYY-MM-DD
(summary of the block in the stage folder's verification.md: real output from this chat)
Full evidence: [verification.md](stages/stage-NN-<slug>/verification.md)
```

## 7. When a stage is too big

If context runs low, stop at a green test boundary, commit `stage-NN (part 1): …`, and write the handoff note marked *partial*, with status `IN PROGRESS`. Note in the stage folder's `worklog.md` exactly where part 1 stopped, so part 2 continues in the same folder. Continue in a new chat with the same kick-off prompt plus "continue from part 1". The stage is `DONE` only after its final part passes full verification. The next stage cannot start before then.
