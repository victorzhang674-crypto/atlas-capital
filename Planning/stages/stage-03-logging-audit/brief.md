# Stage 03 — Logging, run context & audit trail
**Phase:** 0 · **Size:** S · **Depends on:** 02 · **Spec refs:** §3.5, §14

## Goal
Structured logs tagged with a run ID, plus an **append-only audit log** that records every decision-relevant event, which later stages will write to.

## In scope
- `structlog` configuration: JSON in files, pretty output in the console; `get_logger(name)`
- `RunContext` (contextvar): `run_id` (ULID), `mode` (backtest/paper/dev), `as_of`, `config_fingerprint`, `stage`
- `AuditEvent` model: `event_id, run_id, ts, as_of, actor, event_type, payload (JSON), prev_hash, hash`
- `AuditLog` with a hash chain (SHA-256 of prev_hash + canonical JSON), writing to JSONL (`data/runs/<run_id>/audit.jsonl`); `verify_chain()`; DuckDB mirroring is added in Stage 06
- Event-type enum, seeded with: `RUN_STARTED, RUN_FINISHED, DATA_SYNC, ANALYST_REPORT, THESIS, PROPOSAL, ALLOCATION, RISK_DECISION, COMPLIANCE, ORDER, FILL, LIMIT_BREACH, CIRCUIT_BREAKER, OVERRIDE, LESSON`
- `core/errors.py`: base `AtlasError` + `ConfigError`, `LookAheadError`, `DataError`, `LLMError`, `RiskLimitError`, `ComplianceError`

## Out of scope
Remote log shipping and alerting (Stage 62).

## Files
`src/atlas/core/logging.py`, `src/atlas/core/run_context.py`, `src/atlas/core/audit.py`, `src/atlas/core/errors.py`, tests for each

## Key interfaces
```python
with run_context(mode="backtest", as_of=date(2024,1,2)) as ctx: ...
audit = AuditLog(path); audit.record(event_type, actor, payload) -> AuditEvent
AuditLog.verify_chain(path) -> bool
```

## Tasks
1. Errors module + tests (hierarchy).
2. RunContext contextvar + context manager; nested context tests.
3. structlog setup; test that `run_id` is bound to every log line (capture output).
4. AuditLog + hash chain; tamper test (edit a line, then `verify_chain` returns False).
5. Canonical JSON (sorted keys, Decimal/date encoders).

## Tests
Chain integrity; tamper detection; concurrent appends from threads keep the file valid (use a lock); run_id propagation.

## Acceptance criteria
- Every audit event is chained and verifiable; logs carry the run_id.

## Verify
`uv run pytest tests/unit/core -q`
