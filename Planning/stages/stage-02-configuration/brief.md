# Stage 02 — Configuration system & fund mandate
**Phase:** 0 · **Size:** S · **Depends on:** 01 · **Spec refs:** §8, §9, §15

## Goal
One typed, validated configuration object (`AtlasConfig`) loaded from YAML plus env/.env, with profiles (`backtest`, `paper`, `dev`) and the fund mandate and risk limits as data.

## In scope
- `pydantic-settings` based `AtlasConfig` with nested models: `FundConfig` (name, base currency, initial capital = 10_000_000, benchmark), `LimitsConfig` (every limit in spec §9), `PodConfig` (name, enabled, risk_budget, rebalance, universe ref), `DataConfig` (lake path, providers, API key names), `LLMConfig` (providers, tiers, budgets; values only, used in Stage 24), `BacktestConfig` (window, costs)
- YAML files: `config/fund.yaml`, `config/limits.yaml`, `config/pods/*.yaml` (5 pods), `config/universe.yaml`, `config/profiles/{dev,backtest,paper}.yaml`
- Layering: defaults < `fund.yaml`/`limits.yaml`/pods < profile < env vars (`ATLAS__LIMITS__MAX_GROSS=1.5`) < explicit overrides
- Secrets only from env/.env (`ANTHROPIC_API_KEY`, `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, `FRED_API_KEY`, `SEC_USER_AGENT`); `.env.example`
- Validation: pod risk budgets sum to 1.0; net-exposure min < max; drawdown tiers increasing; clear error messages
- `config_fingerprint()` (stable hash) to tag runs

## Out of scope
Hot reload; a UI for config.

## Files
`src/atlas/core/config.py`, `config/**/*.yaml`, `.env.example`, `tests/unit/core/test_config.py`

## Key interfaces
```python
def load_config(profile: str = "dev", overrides: dict | None = None, config_dir: Path | None = None) -> AtlasConfig
class AtlasConfig(BaseSettings): fund: FundConfig; limits: LimitsConfig; pods: dict[str, PodConfig]; data: DataConfig; llm: LLMConfig; backtest: BacktestConfig
def config_fingerprint(cfg: AtlasConfig) -> str  # 12 hex chars, secrets excluded
```

## Tasks
1. TDD the models with defaults; test that the defaults load with no files present.
2. YAML loader + merge order; tests for each layer overriding the one before it.
3. Validators (budgets sum, ranges, tiers); a failing test for each invalid case.
4. Env override with the `__` nested delimiter; test with `monkeypatch.setenv`.
5. Fingerprint excludes secrets; test that two configs differing only in a secret share a fingerprint.
6. Write all YAML files with comments explaining every key.

## Tests
Defaults; layering; each validator; env override; fingerprint stability; secrets never appear in `repr()`.

## Acceptance criteria
- `load_config("backtest")` returns a fully populated config; invalid YAML raises `ConfigError` naming the bad key.
- Every limit in spec §9 exists as a typed field.

## Verify
`uv run pytest tests/unit/core/test_config.py -q`
