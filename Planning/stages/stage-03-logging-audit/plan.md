# Stage 03 — Logging, run context & audit trail · Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

- **Brief:** [brief.md](brief.md)
- **Status:** DONE (2026-10-26; see [verification.md](verification.md))
- **Gate check:** Stage 02 is DONE (its [verification.md](../stage-02-configuration/verification.md) says ALL CRITERIA VERIFIED; evidence block also in `PROGRESS.md`); `uv run pytest -q` → 117 passed, exit 0 (2026-10-26) · raw: [artifacts/gate-check-pytest.txt](artifacts/gate-check-pytest.txt)
- **Execution method:** executing-plans (native, inline TDD), as the kick-off prompt says.

**Goal:** Structured logs tagged with the run ID, a `RunContext` carried in a contextvar, and an append-only, SHA-256 hash-chained JSONL audit log that later stages write decision events to.

**Architecture:** `atlas.core.run_context` holds a frozen `RunContext` in a `ContextVar`; `run_context()` opens a run (new ULID) or a nested scope of the current run. `atlas.core.logging` configures structlog on top of stdlib logging: one processor chain that injects the current run's fields into every record (Atlas and third-party loggers alike), JSON lines to a file, a pretty console renderer. `atlas.core.audit` turns a payload into canonical JSON, chains `hash = sha256(prev_hash + canonical_json(event without hash))`, appends one line per event under a per-file lock, and re-derives the whole chain in `verify_chain()`.

**Tech stack:** structlog (stdlib `ProcessorFormatter` integration, prototyped 2026-10-26 with structlog 26.1.0), python-ulid (prototyped with 4.0.1: `str(ULID())` is 26 chars, `ULID().datetime` is tz-aware UTC), pydantic v2 (`JsonValue`), stdlib `contextvars`, `hashlib`, `json`, `threading`.

**Spec:** [brief.md](brief.md); design spec §3 principle 5 ("everything is a recorded, scored decision"), §3 principle 6 (fail safe), §12 (`core/` holds logging, audit, errors; `data/runs/`), §14. Conventions: stage protocol §4 (errors in `atlas.core.errors`, structlog via `get_logger`, carry the `run_id`, never `datetime.now()` outside `atlas.core.clock`). Later readers checked: Stage 05 (clock, `LookAheadError`), Stage 06 (DuckDB mirror of `AuditLog`), Stages 14/26/44/45/48/55/60 (event types), Stage 64 (chain verification status).

## Global constraints

- Every public class and function has a docstring; units stated where there are any (`ts` is tz-aware UTC; `as_of` is a trading-day `date`).
- mypy strict on `atlas.core.*` (already configured). Pydantic models crossing packages use `ConfigDict(frozen=True, extra="forbid")`.
- **No wall-clock call in Atlas code** (`datetime.now`, `date.today`, `time.time`): `atlas.core.clock` is Stage 05. Times come from library internals only (D2).
- Failures raise exceptions from `atlas.core.errors` (or `TypeError`/`ValueError` for programming errors such as an unencodable payload); nothing is swallowed; a failed `record()` writes nothing.
- No secrets in logs or audit lines; this stage adds no secret handling (scrubber is Stage 66).
- Unit tests write only under `tmp_path`; logging tests restore logging state with `reset_logging()`.
- **One commit for the whole stage:** `stage-03: Logging, run context & audit trail`. No per-task commits (stage protocol §3; Stage 02 precedent).

## Collisions with the overall plan

None with the roadmap or the design spec.

Tension inside the conventions (recorded in the Decisions log as D2): `AuditEvent.ts`, ULIDs and log timestamps need wall-clock time, but stage protocol §4 forbids `datetime.now()` outside `atlas.core.clock`, which Stage 05 creates. **Resolution:** this stage writes no wall-clock call and creates no `clock.py` (Rule 4: no Stage 05 code early). `ts` is the timestamp embedded in the event's ULID (`ULID().datetime`, ms precision, UTC) and log timestamps come from structlog's `TimeStamper(utc=True)`. Stage 05 may route these through its `Clock` later.

## Design decisions (also in PROGRESS.md › Decisions log)

| # | Decision | Why |
|---|---|---|
| D1 | The five new errors subclass `AtlasError` directly (flat). `LookAheadError` is **not** a `DataError`. | A handler that tolerates bad data must never swallow a look-ahead bug. |
| D2 | No wall-clock call in Atlas code: `ts` = the event ULID's timestamp; `run_id`/`event_id` are bare 26-char ULID strings (Stage 04 adds prefixed ids). | See collision note above. `ts` and `event_id` can never disagree. |
| D3 | `RunContext` is a frozen Pydantic model: `run_id`, `mode: Profile` (`dev`/`backtest`/`paper`, the Stage 02 type), `as_of: date \| None`, `config_fingerprint: str \| None`, `stage: str \| None`. | It is read by every package (contract). Reusing `Profile` keeps one list of modes. |
| D4 | `run_context()` at top level opens a **new run**: `mode` defaults to `config.profile` if `config` is given, else `"dev"`; `config_fingerprint = config_fingerprint(config)`. Nested inside a run it is a **child scope**: it inherits everything and may change only `as_of` and `stage`; passing `mode` or `config` when nested raises `AtlasError`. A `mode` that disagrees with `config.profile` raises `AtlasError`. | A run has one id, one mode and one config; a backtest advances `as_of` and the fund day moves through stages. |
| D5 | Logging: structlog → stdlib. One shared processor chain (`add_run_context`, level, logger name, ISO UTC timestamp) also used as `foreign_pre_chain`, so stdlib/third-party lines carry `run_id` too. File handler renders JSON (sorted keys), console handler renders `ConsoleRenderer` (colours only on a TTY). `configure_logging()` is idempotent: it closes and replaces the handlers it installed before; `reset_logging()` removes them. Log level is a parameter (default `"INFO"`), not a config key. | "Every log line carries the run_id" must hold for library logs too. No new config key is needed; the CLI (Stage 65) can expose the level. |
| D6 | `canonical_json(obj)`: `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)`; encoder: `Decimal` → `str(d)` (exact digits; non-finite → `ValueError`), aware `datetime` → UTC ISO string (naive → `ValueError`), `date` → ISO, `Enum` → `.value`, Pydantic model → `model_dump(mode="json")`, anything else (sets included) → `TypeError`. | Deterministic bytes for hashing; money keeps its digits; ambiguous types fail loudly. |
| D7 | Hash chain: `GENESIS_HASH = "0" * 64`; `hash = sha256((prev_hash + canonical_json(body)).encode("utf-8")).hexdigest()` where `body` is the event's JSON dump without `hash`. The payload is stored in its canonical JSON-native form, so the hash is re-derivable from the file alone. | The brief's formula; verification needs no Python types. |
| D8 | `AuditLog(path)` creates parent dirs; if the file has events it **verifies the whole chain first and refuses to open a broken one** (`AtlasError`). State (last hash + `threading.Lock`) is shared per resolved path within the process, so two `AuditLog` objects on one file cannot fork the chain. Each append writes one line, `flush()` + `os.fsync()`. Multi-process writers to one file are not supported (one file per run). | Fail safe: never extend a tampered or half-written log. |
| D9 | `record()` needs an active run context (`run_id`, `as_of` come from it); without one it raises `AtlasError` and writes nothing. `verify_chain(path)`: missing file → `False`; empty file → `True`; any unparsable line, invalid event, broken link or wrong hash → `False`. | An event with no run is malformed. |
| D10 | Plan order differs from the brief's task list: canonical JSON (brief task 5) is built before `AuditLog` (brief task 4), because the hash needs it. | Dependency order; same content. |
| D11 | Path helpers: `run_dir(lake_path, run_id) -> lake_path / "runs" / run_id` (in `run_context.py`) and `audit_path(lake_path, run_id) -> run_dir(...) / "audit.jsonl"` (in `audit.py`), where `lake_path` is `cfg.data.lake_path` (default `data`). | The brief's `data/runs/<run_id>/audit.jsonl`, without hard-coding `data`. |

## File structure

| File | Responsibility |
|---|---|
| `src/atlas/core/errors.py` (modify) | Add `LookAheadError`, `DataError`, `LLMError`, `RiskLimitError`, `ComplianceError` |
| `src/atlas/core/run_context.py` (create) | `RunContext`, `run_context()`, `current_run_context()`, `run_dir()` |
| `src/atlas/core/logging.py` (create) | `configure_logging()`, `reset_logging()`, `get_logger()`, `add_run_context` processor |
| `src/atlas/core/audit.py` (create) | `canonical_json()`, `EventType`, `AuditEvent`, `AuditLog`, `GENESIS_HASH`, `audit_path()` |
| `tests/unit/core/test_{errors,run_context,logging,audit}.py` (create) | Tests per module |
| `pyproject.toml`, `uv.lock` (modify) | Runtime deps `structlog`, `python-ulid` |

## Review focus

1. A payload with `Decimal`, `date`, aware `datetime`, an `Enum`, a nested Pydantic model and non-ASCII text must be recorded, reloaded and verified → `test_rich_payload_round_trips_and_verifies` (Task 5).
2. A payload with `NaN`/`inf`, a naive `datetime` or a `set` must raise and leave the file and chain untouched → `test_bad_payload_writes_nothing` (Task 5).
3. A crash mid-write leaves a partial last line: `verify_chain` must return `False` and re-opening must refuse → `test_truncated_last_line_fails_and_reopen_refuses` (Task 5).
4. Two `AuditLog` objects on the same file, and many threads on one object, must keep one valid chain → `test_two_instances_same_file_share_chain`, `test_concurrent_threads_keep_chain_valid` (Task 5).
5. Context isolation: a new thread starts with no run context, `copy_context()` propagates it, and a nested scope is restored even when its body raises → `test_new_thread_has_no_context`, `test_copy_context_propagates`, `test_nested_restored_after_exception` (Task 2).

---

### Task 1: Error hierarchy

**Files:** modify `src/atlas/core/errors.py`; create `tests/unit/core/test_errors.py`.

**Interfaces:** Produces `AtlasError`, `ConfigError` (unchanged), `LookAheadError`, `DataError`, `LLMError`, `RiskLimitError`, `ComplianceError`, each with a docstring.

- [x] **Step 1: Write failing tests**
  - `test_every_error_is_an_atlas_error` — parametrized over the six subclasses: `issubclass(cls, AtlasError)` and `issubclass(AtlasError, Exception)`; `pytest.raises(AtlasError)` catches `cls("msg")`.
  - `test_errors_are_distinct_siblings` — for every ordered pair `a != b`: `not issubclass(a, b)` (pins D1).
  - `test_errors_have_docstrings` — `cls.__doc__` is non-empty for all seven.
  - `test_error_keeps_message` — `str(RiskLimitError("gross 2.1 > 2.0")) == "gross 2.1 > 2.0"`.
- [x] **Step 2: Run** `uv run pytest tests/unit/core/test_errors.py -q` → FAIL (`ImportError: cannot import name 'LookAheadError'`).
- [x] **Step 3: Implement** the five classes, each `class X(AtlasError)` with a one-line docstring; update the module docstring (drop "Stage 03 extends").
- [x] **Step 4: Run** the same command → PASS; full suite → green.

### Task 2: Run context

**Files:** modify `pyproject.toml`/`uv.lock` (`uv add "python-ulid>=3.0"`); create `src/atlas/core/run_context.py`, `tests/unit/core/test_run_context.py`.

**Interfaces:**
- Consumes: `atlas.core.config.{AtlasConfig, Profile, config_fingerprint}`; `AtlasError`.
- Produces:
  - `class RunContext(BaseModel)` frozen: `run_id: str`, `mode: Profile`, `as_of: date | None`, `config_fingerprint: str | None`, `stage: str | None`.
  - `run_context(*, mode: Profile | None = None, as_of: date | None = None, config: AtlasConfig | None = None, stage: str | None = None) -> contextlib.AbstractContextManager[RunContext]` (a `@contextmanager` generator).
  - `current_run_context() -> RunContext | None`.
  - `run_dir(lake_path: Path, run_id: str) -> Path`.

- [x] **Step 1: Write failing tests**
  - `test_top_level_opens_new_run` — inside `run_context(mode="backtest", as_of=date(2024, 1, 2))`: `len(ctx.run_id) == 26`, `ctx.mode == "backtest"`, `ctx.as_of == date(2024, 1, 2)`, `ctx.stage is None`, `ctx.config_fingerprint is None`, `current_run_context() is ctx`; after exit `current_run_context() is None`.
  - `test_default_mode_is_dev`; `test_each_run_gets_new_id` (two sequential runs differ).
  - `test_config_sets_mode_and_fingerprint` — `cfg = AtlasConfig(profile="paper")`: `ctx.mode == "paper"`, `ctx.config_fingerprint == config_fingerprint(cfg)`.
  - `test_mode_conflicting_with_config_raises` — `run_context(mode="dev", config=AtlasConfig(profile="paper"))` → `AtlasError`.
  - `test_invalid_mode_rejected` — `run_context(mode="live")` → `pydantic.ValidationError` (paper-only: no live mode).
  - `test_nested_inherits_and_overrides` — outer `mode="backtest", as_of=d1`; inner `as_of=d2, stage="risk"`: same `run_id`/`mode`/fingerprint, `as_of == d2`, `stage == "risk"`; inner without args keeps `as_of == d1`; after inner exits `current_run_context() is outer`.
  - `test_nested_cannot_change_mode_or_config` — nested `mode=` or `config=` → `AtlasError`.
  - `test_nested_restored_after_exception` — raise inside inner; `current_run_context() is outer` afterwards.
  - `test_new_thread_has_no_context` — `threading.Thread` started inside a run sees `None`.
  - `test_copy_context_propagates` — `contextvars.copy_context().run(current_run_context)` in a thread returns the outer ctx.
  - `test_run_context_is_frozen` — assigning `ctx.stage` raises `ValidationError`.
  - `test_run_dir` — `run_dir(Path("data"), "01ABC") == Path("data/runs/01ABC")`.
- [x] **Step 2: Run** `uv run pytest tests/unit/core/test_run_context.py -q` → FAIL (`ModuleNotFoundError`).
- [x] **Step 3: Implement** per D3/D4: `_CURRENT: ContextVar[RunContext | None]`; set with a token, `reset(token)` in `finally`.
- [x] **Step 4: Run** → PASS; `uv run mypy src/atlas` → Success.

### Task 3: Structured logging

**Files:** modify `pyproject.toml`/`uv.lock` (`uv add "structlog>=24.1"`); create `src/atlas/core/logging.py`, `tests/unit/core/test_logging.py`.

**Interfaces:**
- Consumes: `current_run_context()`.
- Produces:
  - `configure_logging(*, level: str = "INFO", log_file: Path | None = None, console: TextIO | None = None) -> None` (`console` defaults to `sys.stderr` at call time; `log_file` parents are created).
  - `reset_logging() -> None`.
  - `get_logger(name: str) -> structlog.stdlib.BoundLogger` (configures structlog's processor chain on first use if `configure_logging` was never called).
  - `add_run_context(logger, method_name, event_dict) -> event_dict` — sets `run_id`, `mode`, and when not `None` `as_of` (ISO date string) and `stage`; no-op outside a run.

- [x] **Step 1: Write failing tests** (autouse fixture calls `reset_logging()` after each test)
  - `test_every_file_line_is_json_with_run_id` — `configure_logging(log_file=tmp_path / "atlas.log", console=io.StringIO())`; inside `run_context(mode="backtest", as_of=date(2024, 1, 2))`: `get_logger("atlas.test").info("first", x=1)`; nested `stage="risk"`: `.warning("second")`; `logging.getLogger("thirdparty").warning("third %s", "x")`. Every file line parses as JSON with `run_id == ctx.run_id`, `mode == "backtest"`, `as_of == "2024-01-02"`, `level`, `logger`, `timestamp` (UTC, ends with `Z`); the `second` line has `stage == "risk"`; the `x` field is `1`.
  - `test_console_is_pretty_text` — console buffer contains `first` and `run_id=`; its first line is not valid JSON.
  - `test_outside_run_has_no_run_id` — line has no `run_id` key.
  - `test_level_filters` — `level="WARNING"`: `info` line absent, `warning` present.
  - `test_reconfigure_does_not_duplicate` — configure twice (second file `b.log`): one message → exactly one line in `b.log`, none added to `a.log`.
  - `test_exception_is_rendered_in_json` — `log.exception("boom")` inside `except ValueError` → line has `exception` containing `ValueError`.
  - `test_get_logger_before_configure` — `get_logger("x").info("ok")` does not raise.
- [x] **Step 2: Run** `uv run pytest tests/unit/core/test_logging.py -q` → FAIL (`ModuleNotFoundError`).
- [x] **Step 3: Implement** per D5 (`ProcessorFormatter` with `foreign_pre_chain`, `remove_processors_meta`, `format_exc_info`, `JSONRenderer(sort_keys=True)` for the file, `ConsoleRenderer(colors=stream.isatty())` for the console); handlers attached to the root logger and tracked in a module list; `cache_logger_on_first_use=False`.
- [x] **Step 4: Run** → PASS; mypy → Success.

### Task 4: Canonical JSON

**Files:** create `src/atlas/core/audit.py` (this part), `tests/unit/core/test_audit.py`.

**Interfaces:** Produces `canonical_json(obj: object) -> str` (D6).

- [x] **Step 1: Write failing tests**
  - `test_sorted_compact` — `canonical_json({"b": 1, "a": [1, 2]}) == '{"a":[1,2],"b":1}'`; nested dict keys sorted too.
  - `test_same_content_same_bytes` — dicts built in different insertion orders give equal strings.
  - `test_decimal_keeps_digits` — `{"x": Decimal("1.10")}` → `'{"x":"1.10"}'`; `Decimal("NaN")` → `ValueError`.
  - `test_dates_and_datetimes` — `date(2024, 1, 2)` → `"2024-01-02"`; `datetime(2024, 1, 2, 9, 30, tzinfo=ZoneInfo("America/New_York"))` → `"2024-01-02T14:30:00+00:00"`; naive datetime → `ValueError`.
  - `test_enum_and_model` — `EventType.ORDER` → `"ORDER"`; a small frozen Pydantic model → its JSON dump.
  - `test_rejects_nan_inf_and_unknown` — `float("nan")`, `float("inf")` → `ValueError`; `{1, 2}` and `object()` → `TypeError`.
  - `test_non_ascii_kept` — `"€"` stays as is (no `€`).
- [x] **Step 2: Run** `uv run pytest tests/unit/core/test_audit.py -q` → FAIL (`ModuleNotFoundError`).
- [x] **Step 3: Implement** `canonical_json` and `EventType` (needed by the enum test): `class EventType(StrEnum)` with exactly `RUN_STARTED, RUN_FINISHED, DATA_SYNC, ANALYST_REPORT, THESIS, PROPOSAL, ALLOCATION, RISK_DECISION, COMPLIANCE, ORDER, FILL, LIMIT_BREACH, CIRCUIT_BREAKER, OVERRIDE, LESSON`, each value equal to its name. Check `datetime` before `date` in the encoder.
- [x] **Step 4: Run** → PASS.

### Task 5: Audit log and hash chain

**Files:** extend `src/atlas/core/audit.py`, `tests/unit/core/test_audit.py`.

**Interfaces:**
- Consumes: `canonical_json`, `EventType`, `current_run_context()`, `run_dir()`, `AtlasError`.
- Produces:
  - `GENESIS_HASH: str = "0" * 64`.
  - `class AuditEvent(BaseModel)` frozen, extra forbid: `event_id: str`, `run_id: str`, `ts: AwareDatetime` (UTC), `as_of: date | None`, `actor: str` (min length 1), `event_type: EventType`, `payload: dict[str, JsonValue]`, `prev_hash: str` (64 lowercase hex), `hash: str` (64 lowercase hex).
  - `class AuditLog`: `__init__(self, path: Path)`, `path` property, `record(self, event_type: EventType, actor: str, payload: Mapping[str, Any]) -> AuditEvent`, `@staticmethod verify_chain(path: Path) -> bool`.
  - `audit_path(lake_path: Path, run_id: str) -> Path`.

- [x] **Step 1: Write failing tests** (helper `_lines(path)` returns parsed JSON lines)
  - `test_seeded_event_types` — `{e.value for e in EventType}` equals the 15 names above.
  - `test_record_chains_events` — in a run (`as_of=date(2024, 1, 2)`), record `RUN_STARTED` then `ORDER`: first `prev_hash == GENESIS_HASH`, second `prev_hash == first.hash`; each `hash == sha256((prev_hash + canonical_json(dump without hash)).encode()).hexdigest()`; `run_id`/`as_of` from the context; `ts.tzinfo` is UTC; file has 2 lines equal to the returned events' dumps; `verify_chain(path) is True`.
  - `test_record_outside_run_raises` — `AtlasError`, file not created.
  - `test_empty_actor_rejected` — `actor=""` → `ValidationError`, file unchanged.
  - `test_rich_payload_round_trips_and_verifies` (Review focus 1).
  - `test_bad_payload_writes_nothing` (Review focus 2) — after one good event, each bad payload raises (`ValueError`/`TypeError`); file bytes unchanged; next good event chains to the first; `verify_chain` True.
  - Tamper tests, each starting from a 3-event log → `verify_chain is False`: `test_tamper_edited_payload`, `test_tamper_deleted_line`, `test_tamper_swapped_lines`, `test_tamper_rehashed_line_breaks_next_link` (edit line 2's payload and recompute its own hash; line 3's `prev_hash` no longer matches), `test_tamper_invalid_json_line`.
  - `test_truncated_last_line_fails_and_reopen_refuses` (Review focus 3) — cut the last line in half: `verify_chain` False; `AuditLog(path)` → `AtlasError`.
  - `test_verify_missing_and_empty` — missing → False; empty file → True.
  - `test_reopen_continues_chain` — new `AuditLog(path)` on a 2-event file; the next event's `prev_hash` is event 2's hash; verify True.
  - `test_two_instances_same_file_share_chain` (Review focus 4) — `a`, `b` on one path, alternate 10 records; verify True; 10 lines.
  - `test_concurrent_threads_keep_chain_valid` (Review focus 4) — 8 threads × 50 `record()` calls on one `AuditLog`, each thread running in `contextvars.copy_context()`; 400 lines, all parse, verify True, `event_id`s unique.
  - `test_audit_path` — `audit_path(Path("data"), "01ABC") == Path("data/runs/01ABC/audit.jsonl")`; recording to it creates the directories.
- [x] **Step 2: Run** `uv run pytest tests/unit/core/test_audit.py -q` → FAIL (`ImportError: AuditLog`).
- [x] **Step 3: Implement** per D7–D9. `record()`: read the run context (else `AtlasError`), normalise the payload with `json.loads(canonical_json(payload))` **before** taking the lock, then under the per-path lock: new ULID → `event_id`/`ts`, build the body, hash, validate `AuditEvent`, append `canonical_json(event dump) + "\n"`, flush, fsync, update the shared last hash. Per-path state: a module dict `{resolved path: _ChainState(lock, last_hash)}` guarded by a module lock; `__init__` re-verifies and resets the state under the path lock.
- [x] **Step 4: Run** → PASS; mypy → Success; full suite green.

### Task 6: Definition of Done

- [x] Brief's Verify: `uv run pytest tests/unit/core -q` → all pass; save to `artifacts/verify-tests-core.txt`.
- [x] `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src/atlas`, `make check` → green; raw output in `artifacts/`.
- [x] Docstring check over the four modules (public items without a docstring = `[]`) → `artifacts/docstrings.txt`.
- [x] Wall-clock grep: no `datetime.now`/`date.today`/`time.time` in `src/atlas` → `artifacts/wallclock-grep.txt`.
- [x] Acceptance demo: a small script opens a run, logs and records events to a temp dir, prints the log lines' `run_id`s and `verify_chain` before/after a tamper → `artifacts/acceptance-demo.txt`.
- [x] Write `verification.md` (ALL CRITERIA VERIFIED only if every box is ticked), then `PROGRESS.md` (decisions D1–D11, handoff, evidence summary, tick DONE), then the single commit.
