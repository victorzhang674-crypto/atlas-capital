# Stage 02 — Configuration system & fund mandate · Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

- **Brief:** [brief.md](brief.md)
- **Status:** DONE (2026-10-26; see [verification.md](verification.md); review fixes and rulings in [worklog.md](worklog.md))
- **Gate check:** Stage 01 is DONE (its [verification.md](../stage-01-project-scaffolding/verification.md) says ALL CRITERIA VERIFIED); `uv run pytest -q` → 25 passed, exit 0 (2026-10-26) · raw: [artifacts/gate-check-pytest.txt](artifacts/gate-check-pytest.txt)

**Goal:** One typed, validated, immutable `AtlasConfig` loaded from layered YAML plus env/.env, with the fund mandate (spec §8) and every risk limit (spec §9) held as data.

**Architecture:** `atlas.core.config` holds frozen Pydantic v2 models with code defaults equal to the spec. `load_config()` reads each layer as a plain dict (YAML files, the profile file, `.env`, the process env, explicit overrides), deep-merges them in that order and validates once. Every failure becomes `atlas.core.errors.ConfigError` with the dotted key in its message. Secrets are read only from env/.env into `SecretStr` fields and left out of `config_fingerprint()`.

**Tech stack:** pydantic ≥ 2.9, pydantic-settings ≥ 2.6 (`EnvSettingsSource`, `DotEnvSettingsSource`), PyYAML ≥ 6.0 (`safe_load` with a duplicate-key check), python-dotenv ≥ 1.0 (raw `.env` scan), pytest `monkeypatch`.

**Spec:** [brief.md](brief.md); design spec §8 (pods), §9 (limits), §15 A6–A9 ([00-design-spec.md](../../00-design-spec.md)). Field names that later stages will read come from their briefs: pod ids (Stages 38–40), LLM budget names (Stage 24), the ETF list (Stage 08) and cost values (Stage 51).

## Global constraints

- Weights and limits are **fractions of NAV** (0.05 = 5%). Drawdown thresholds are **positive magnitudes** (0.05 means a −5% drawdown). Short caps are positive magnitudes.
- Use `Decimal` for money (`initial_capital`, `commission_per_share`) and `float` for analytics values.
- Every model has `ConfigDict(frozen=True, extra="forbid")`. Every public class and function has a docstring that states its units.
- mypy is strict on `atlas.core.*`. Add `plugins = ["pydantic.mypy"]` to `[tool.mypy]`.
- Never call `datetime.now()`; config has no clock.
- No secret value may appear in `repr()`, `str()`, `model_dump_json()`, log lines, error messages or the fingerprint.
- Unit tests read nothing from the developer's real environment or `.env`. An autouse fixture clears `ATLAS__*` and the five secret variables and `chdir`s to `tmp_path`.
- **One commit for the whole stage** (stage protocol §3): `stage-02: Configuration system & fund mandate`. There are no per-task commits. If context runs low, use the part-commit rule in protocol §7.

## Collisions with the overall plan

1. **`ConfigError` belongs to Stage 03.** Stage 03's brief creates `core/errors.py` with the full hierarchy, but Stage 02's acceptance criterion needs `ConfigError`, and conventions require custom exceptions in `atlas.core.errors`. **Resolution:** Stage 02 creates `src/atlas/core/errors.py` with **only** `AtlasError` and `ConfigError`. Stage 03 adds `LookAheadError`, `DataError`, `LLMError`, `RiskLimitError`, `ComplianceError` and their hierarchy tests. The roadmap and design spec are silent on this (brief vs brief). Logged in the Decisions log.

No collision with the roadmap or the design spec.

## Design decisions (also in PROGRESS.md › Decisions log)

| # | Decision | Why |
|---|---|---|
| D1 | YAML layout: `fund.yaml` holds top-level sections (`fund`, `data`, `llm`, `backtest`). `limits.yaml` is the body of `limits`. `universe.yaml` is the body of `data.universes` (id → universe). `pods/<id>.yaml` is the body of `pods.<id>`, so the file stem is the pod id. `profiles/<p>.yaml` holds top-level sections. | One obvious home for every key. Matches the brief's file list. |
| D2 | `AtlasConfig` adds two fields beyond the brief's interface: `profile: Literal["dev","backtest","paper"]` (set by `load_config`) and `secrets: Secrets`. | Stage 03's `RunContext.mode` needs the profile. Brief Tasks 5 and 6 require secrets to live on the config. |
| D3 | Secret env-var names are the field names of `Secrets` (single source of truth). `DataConfig` does not duplicate them as "API key names". | Two copies of the names would drift. The brief lists the five names exactly. |
| D4 | Merge rule: mappings merge recursively. Lists and scalars are **replaced** whole. | Lets you override one limit without repeating the others, while a tier list stays explicit. |
| D5 | Pod budgets: the sum over **all** configured pods must be 1.0 (`abs_tol=1e-9`). A disabled pod's budget is left unused. | Fail safe: the book can be under-allocated but never over-allocated. Ablations can disable a pod without rewriting budgets. |
| D6 | `secrets` and `profile` are reserved keys. They are rejected in YAML, overrides and `ATLAS__*` vars. An `ATLAS__<section>__…` variable whose section is unknown raises `ConfigError`. | "Secrets only from env/.env." A typo'd env var must not be silently ignored (pydantic-settings drops it; checked in a prototype on 2026-10-26). |
| D7 | Paths: `config_dir` defaults to `Path.cwd() / "config"`, and a missing directory raises `ConfigError`. `.env` is read from `config_dir.parent / ".env"`. `data.lake_path` stays relative (`data`) and is not resolved. | Deterministic in tests. The fingerprint does not change between machines or worktrees. |
| D8 | Provisional LLM values, to be revisited in Stage 24/56: `per_run_usd = 25.0`, `per_agent_call_max_tokens = 8000`. `daily_usd = 5.0` comes from A8. Cost defaults: single `half_spread_bps = 5.0` (Stage 51 adds the market-cap bucket table), `impact_k = 0.1`, `commission_per_share = 0`, `borrow_gc_annual = 0.003`. | The brief asks for budgets and costs as values. These come from the spec and briefs where they give a number; otherwise they are marked provisional in the YAML comments. |
| D9 | YAML is parsed with a `SafeLoader` subclass that rejects duplicate keys. | PyYAML silently keeps the last duplicate. A risk-limits file must not do that. |

## File structure

| File | Responsibility |
|---|---|
| `pyproject.toml`, `uv.lock` (modify) | Runtime deps `pydantic`, `pydantic-settings`, `pyyaml`, `python-dotenv`; dev dep `types-PyYAML`; mypy pydantic plugin |
| `src/atlas/core/errors.py` (create) | `AtlasError`, `ConfigError` only (Collision 1) |
| `src/atlas/core/config.py` (create) | Models, defaults, validators, `load_config`, `config_fingerprint`, private YAML/env helpers |
| `tests/unit/core/test_config.py` (create) | Every test in this stage (the brief's Verify command runs this file) |
| `config/fund.yaml`, `config/limits.yaml`, `config/universe.yaml`, `config/pods/{fundamental_ls,systematic,statarb,global_macro,event_driven}.yaml`, `config/profiles/{dev,backtest,paper}.yaml` (create) | Commented data; base values equal the code defaults |
| `.env.example` (create) | The five secret names, values empty, with comments |

Test helpers, defined once at the top of `test_config.py` and used by every task:

```python
SECRET_VARS = ("ANTHROPIC_API_KEY", "ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY", "FRED_API_KEY", "SEC_USER_AGENT")

@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):  # drop ATLAS__* (any case) and SECRET_VARS; chdir(tmp_path)

def write_config(root: Path, files: dict[str, str]) -> Path:
    """Write {relative path: YAML text} under root/'config'; return that config dir (created even if files is empty)."""
```

## Review focus

The inputs and failure modes below are implied by the spec but no brief task tests them. Each line names the task that adds its test.

1. **A `.env` holding the real secret keys** (`FRED_API_KEY=…`) must load and fill `secrets`, not fail `extra="forbid"`. The prototype showed `DotEnvSettingsSource` returns unprefixed keys as extras. → Task 4 `test_dotenv_with_secrets_loads`
2. **A typo'd env section** (`ATLAS__LIMTS__MAX_GROSS=1.5`) raises `ConfigError` naming the variable instead of being silently ignored. → Task 4 `test_unknown_env_section_raises`
3. **A percent slip** (`max_single_long: 5` meaning 5%) is rejected with the dotted key, because fraction fields carry upper bounds. → Task 3 `test_percent_instead_of_fraction_rejected`
4. **A duplicated key in a YAML file** (two `max_gross:` lines) raises `ConfigError` naming the key and the file. → Task 2 `test_duplicate_yaml_key_raises`
5. **An empty YAML file, or one whose top level is a list**, is handled: empty means "no layer", a list raises `ConfigError` naming the file. → Task 2 `test_empty_yaml_is_no_layer`, `test_non_mapping_yaml_raises`

---

## Tasks (execute in order; tick only after the task's tests have run and passed in this chat)

### Task 1 — Dependencies, errors and models with defaults

**Files:** modify `pyproject.toml`, `uv.lock` · create `src/atlas/core/errors.py`, `src/atlas/core/config.py`, `tests/unit/core/test_config.py`

**Interfaces — produces** (all models subclass a private `_Frozen(BaseModel)` with `frozen=True, extra="forbid"`):

```python
class AtlasError(Exception)                     # atlas.core.errors
class ConfigError(AtlasError)

Profile = Literal["dev", "backtest", "paper"]; PROFILES: tuple[Profile, ...]
class FundConfig: name: str = "Atlas Capital"; base_currency: Literal["USD"] = "USD"
                  initial_capital: Decimal = Decimal("10000000"); benchmark: str = "SPY"
class DrawdownTier: drawdown: float; degross_frac: float; block_new_names: bool; halt: bool  # halt ⇒ owner reset
class PodStops: halve_budget_at: float = 0.08; budget_multiplier: float = 0.5; freeze_at: float = 0.12
class LimitsConfig: target_vol_annual=0.10; max_gross=2.00; net_min=-0.10; net_max=0.40
                    beta_min=-0.10; beta_max=0.30; max_single_long=0.05; max_single_short=0.03
                    max_sector_net=0.15; max_style_exposure_sigma=0.5
                    max_order_adv_frac=0.05; adv_window_days: int = 20
                    max_liquidation_days=3.0; liquidation_adv_frac=0.10
                    var_confidence=0.95; max_var=0.015; cvar_confidence=0.975; max_cvar=0.025
                    max_stress_loss=0.15; drawdown_tiers: list[DrawdownTier]; pod_stops: PodStops
                    # all unannotated above are float
class PodConfig: name: str; enabled: bool = True; risk_budget: float
                 rebalance: Literal["daily", "weekly", "event"]; universe: str
class UniverseConfig: description: str; index: str | None = None; tickers: list[str] = []
class DataProviders: prices: Literal["yfinance","alpaca"]="yfinance"; fundamentals: Literal["sec_edgar"]="sec_edgar"
                     macro: Literal["fred"]="fred"; news: Literal["alpaca","yahoo"]="alpaca"
class DataConfig: lake_path: Path = Path("data"); providers: DataProviders; universes: dict[str, UniverseConfig]
class LLMTiers: deep: str = "claude-opus-5-5"; quick: str = "claude-haiku-5-5"
class LLMBudgets: daily_usd: float = 5.0; per_run_usd: float = 25.0; per_agent_call_max_tokens: int = 8000
class LLMConfig: provider: Literal["anthropic", "openai_compatible"] = "anthropic"; tiers: LLMTiers; budgets: LLMBudgets
class CostConfig: half_spread_bps: float = 5.0; impact_k: float = 0.1
                  commission_per_share: Decimal = Decimal("0"); borrow_gc_annual: float = 0.003
class BacktestConfig: start: date = date(2018, 1, 1); in_sample_end: date = date(2021, 12, 31)
                      end: date | None = None  # None = latest available; costs: CostConfig
class Secrets: anthropic_api_key, alpaca_api_key_id, alpaca_api_secret_key, fred_api_key,
               sec_user_agent: SecretStr | None = None
class AtlasConfig(BaseSettings): profile: Profile = "dev"; fund; limits; pods: dict[str, PodConfig]
                                 data; llm; backtest; secrets: Secrets
    # model_config: env_prefix="ATLAS__", env_nested_delimiter="__", frozen=True, extra="forbid"
    # settings_customise_sources returns (init_settings,) only → AtlasConfig() is pure defaults
```

Default `drawdown_tiers`: `(0.05, 0.25, False, False)`, `(0.10, 0.50, True, False)`, `(0.15, 1.00, True, True)` as `(drawdown, degross_frac, block_new_names, halt)`.
Default pods (id: name, budget, rebalance, universe): `fundamental_ls`: "Fundamental L/S", 0.30, weekly, sp500 · `systematic`: "Systematic Factor", 0.25, weekly, sp500 · `statarb`: "Stat-Arb", 0.15, daily, sp500 · `global_macro`: "Global Macro", 0.20, weekly, macro_etfs · `event_driven`: "Event-Driven", 0.10, event, sp500.
Default universes: `sp500` = `{description: "S&P 500 constituents (point-in-time membership from Stage 08)", index: "SP500"}`; `macro_etfs` = the 30 tickers `SPY QQQ IWM EFA EEM XLB XLC XLE XLF XLI XLK XLP XLRE XLU XLV XLY TLT IEF SHY LQD HYG GLD SLV USO DBC UUP FXE FXY VNQ BIL`.

- [x] **Step 1: Add deps.** `uv add "pydantic>=2.9" "pydantic-settings>=2.6" "pyyaml>=6.0" "python-dotenv>=1.0"` and `uv add --group dev "types-PyYAML>=6.0"`. Add `plugins = ["pydantic.mypy"]` under `[tool.mypy]`. Run `uv run pytest -q` → 25 passed.
- [x] **Step 2: Write the failing tests** in `tests/unit/core/test_config.py`:

```python
def test_config_error_is_atlas_error():
    assert issubclass(ConfigError, AtlasError)

def test_defaults_without_files():
    cfg = AtlasConfig()
    assert cfg.profile == "dev"
    assert cfg.fund == FundConfig(name="Atlas Capital", base_currency="USD",
                                  initial_capital=Decimal("10000000"), benchmark="SPY")
    assert cfg.llm.tiers.deep == "claude-opus-5-5" and cfg.llm.budgets.daily_usd == 5.0
    assert cfg.backtest.start == date(2018, 1, 1) and cfg.backtest.in_sample_end == date(2021, 12, 31)
    assert cfg.backtest.end is None and cfg.backtest.costs.impact_k == 0.1
    assert cfg.secrets.anthropic_api_key is None

SPEC_9 = {"target_vol_annual": 0.10, "max_gross": 2.0, "net_min": -0.10, "net_max": 0.40,
          "beta_min": -0.10, "beta_max": 0.30, "max_single_long": 0.05, "max_single_short": 0.03,
          "max_sector_net": 0.15, "max_style_exposure_sigma": 0.5, "max_order_adv_frac": 0.05,
          "adv_window_days": 20, "max_liquidation_days": 3.0, "liquidation_adv_frac": 0.10,
          "var_confidence": 0.95, "max_var": 0.015, "cvar_confidence": 0.975, "max_cvar": 0.025,
          "max_stress_loss": 0.15}

@pytest.mark.parametrize(("field", "value"), SPEC_9.items())
def test_limits_cover_spec_section_9(field, value):
    assert getattr(LimitsConfig(), field) == value
    assert LimitsConfig.model_fields[field].annotation is (int if field == "adv_window_days" else float)

def test_drawdown_tiers_and_pod_stops_defaults():
    tiers = [(t.drawdown, t.degross_frac, t.block_new_names, t.halt) for t in LimitsConfig().drawdown_tiers]
    assert tiers == [(0.05, 0.25, False, False), (0.10, 0.50, True, False), (0.15, 1.0, True, True)]
    assert LimitsConfig().pod_stops == PodStops(halve_budget_at=0.08, budget_multiplier=0.5, freeze_at=0.12)

def test_default_pods_match_spec_section_8():
    pods = AtlasConfig().pods
    assert {k: (p.risk_budget, p.rebalance, p.universe) for k, p in pods.items()} == {
        "fundamental_ls": (0.30, "weekly", "sp500"), "systematic": (0.25, "weekly", "sp500"),
        "statarb": (0.15, "daily", "sp500"), "global_macro": (0.20, "weekly", "macro_etfs"),
        "event_driven": (0.10, "event", "sp500")}
    assert all(p.enabled for p in pods.values())

def test_default_macro_universe_is_stage_08_list():
    assert AtlasConfig().data.universes["macro_etfs"].tickers == MACRO_ETFS  # the 30 above, in order

def test_config_is_frozen():
    with pytest.raises(ValidationError):
        AtlasConfig().limits.max_gross = 1.0  # type: ignore[misc]
```

- [x] **Step 3: Run** `uv run pytest tests/unit/core/test_config.py -q` → FAIL (`ModuleNotFoundError: atlas.core.config`).
- [x] **Step 4: Implement** `errors.py` and the models in `config.py` with the interfaces above. Use `Field(default_factory=…)` for nested and dict defaults.
- [x] **Step 5: Run** the same command → PASS. Then run `uv run mypy src/atlas` → Success.
- [x] **Step 6: Worklog entry** in `worklog.md` (files touched, commands, results).

### Task 2 — YAML loader and merge order

**Files:** modify `src/atlas/core/config.py`, `tests/unit/core/test_config.py`

**Interfaces — produces:** `load_config(profile: str = "dev", overrides: dict[str, Any] | None = None, config_dir: Path | None = None) -> AtlasConfig`. Private helpers: `_read_yaml(path: Path) -> dict[str, Any]` (a missing or empty file returns `{}`) and `_deep_merge(*layers: Mapping[str, Any]) -> dict[str, Any]` (D4).

Pinned messages: unknown profile → `f"Unknown profile {profile!r}; expected one of: dev, backtest, paper"`; missing directory → `f"config directory not found: {path}"`.

Layer order (lowest first): code defaults < `fund.yaml` < `limits.yaml` < `universe.yaml` < `pods/*.yaml` (sorted by stem) < `profiles/<profile>.yaml` < `.env` < process env < `overrides` < `{"profile": profile}`. Env layers arrive in Task 4. On `pydantic.ValidationError`, raise `ConfigError` whose message starts `Invalid configuration (profile '<p>')` followed by one line per error, `<dotted.loc>: <msg>`, then chain the original with `from exc`.

- [x] **Step 1: Write the failing tests:**

```python
def test_load_config_empty_dir_equals_defaults(tmp_path):
    assert load_config(config_dir=write_config(tmp_path, {})) == AtlasConfig()

def test_missing_config_dir_raises(tmp_path):
    with pytest.raises(ConfigError, match="config directory"):
        load_config(config_dir=tmp_path / "nope")

def test_unknown_profile_raises(tmp_path):
    with pytest.raises(ConfigError, match="'live'.*dev, backtest, paper"):
        load_config("live", config_dir=write_config(tmp_path, {}))

def test_default_config_dir_is_cwd_config(tmp_path):  # autouse fixture already chdir'd to tmp_path
    write_config(tmp_path, {"limits.yaml": "max_gross: 1.8\n"})
    assert load_config().limits.max_gross == 1.8

def test_layer_order(tmp_path):
    d = write_config(tmp_path, {
        "fund.yaml": "fund: {benchmark: QQQ}\nllm: {budgets: {daily_usd: 4.0}}\n",
        "limits.yaml": "max_gross: 1.8\nmax_var: 0.012\n",
        "pods/statarb.yaml": "rebalance: weekly\n",
        "profiles/backtest.yaml": "limits: {max_gross: 1.6}\nllm: {budgets: {daily_usd: 3.0}}\n"})
    cfg = load_config("backtest", overrides={"limits": {"max_gross": 1.4}}, config_dir=d)
    assert cfg.profile == "backtest"
    assert cfg.fund.benchmark == "QQQ"           # base file beats default
    assert cfg.limits.max_var == 0.012           # untouched by later layers
    assert cfg.pods["statarb"].rebalance == "weekly" and cfg.pods["statarb"].risk_budget == 0.15
    assert cfg.llm.budgets.daily_usd == 3.0      # profile beats base
    assert cfg.limits.max_gross == 1.4           # override beats profile
    assert load_config("dev", config_dir=d).limits.max_gross == 1.8   # other profile file not read

def test_lists_are_replaced_not_merged(tmp_path):
    d = write_config(tmp_path, {"profiles/dev.yaml":
        "limits:\n  drawdown_tiers:\n    - {drawdown: 0.04, degross_frac: 0.5, block_new_names: true, halt: true}\n"})
    assert len(load_config(config_dir=d).limits.drawdown_tiers) == 1

def test_invalid_value_names_dotted_key(tmp_path):
    d = write_config(tmp_path, {"pods/statarb.yaml": "risk_budget: lots\n"})
    with pytest.raises(ConfigError, match=r"pods\.statarb\.risk_budget"):
        load_config(config_dir=d)

def test_unknown_key_names_dotted_key(tmp_path):
    d = write_config(tmp_path, {"limits.yaml": "max_gros: 1.5\n"})
    with pytest.raises(ConfigError, match=r"limits\.max_gros"):
        load_config(config_dir=d)

def test_malformed_yaml_names_file_and_line(tmp_path):
    d = write_config(tmp_path, {"limits.yaml": "max_gross: 1.5\nnet_min: [oops\n"})
    with pytest.raises(ConfigError, match=r"limits\.yaml.*line \d+"):
        load_config(config_dir=d)

def test_duplicate_yaml_key_raises(tmp_path):                       # Review focus 4
    d = write_config(tmp_path, {"limits.yaml": "max_gross: 1.5\nmax_gross: 3.0\n"})
    with pytest.raises(ConfigError, match=r"limits\.yaml.*duplicate key 'max_gross'"):
        load_config(config_dir=d)

def test_empty_yaml_is_no_layer(tmp_path):                          # Review focus 5
    d = write_config(tmp_path, {"limits.yaml": "", "profiles/dev.yaml": "# only a comment\n"})
    assert load_config(config_dir=d) == AtlasConfig()

def test_non_mapping_yaml_raises(tmp_path):                         # Review focus 5
    d = write_config(tmp_path, {"limits.yaml": "- 1\n- 2\n"})
    with pytest.raises(ConfigError, match=r"limits\.yaml.*mapping"):
        load_config(config_dir=d)
```

- [x] **Step 2: Run** `uv run pytest tests/unit/core/test_config.py -q` → the new tests FAIL (`load_config` not defined).
- [x] **Step 3: Implement** `load_config`, `_read_yaml` and `_deep_merge`. `_read_yaml` uses a `yaml.SafeLoader` subclass whose mapping constructor raises on a repeated key (D9). It turns `yaml.YAMLError` into `ConfigError(f"{path}: invalid YAML at line {mark.line + 1}: {problem}")`. Pod layers are keyed by `path.stem`.
- [x] **Step 4: Run** the same command → PASS.
- [x] **Step 5: Worklog entry.**

### Task 3 — Validators

**Files:** modify `src/atlas/core/config.py`, `tests/unit/core/test_config.py`

**Interfaces — produces:** the `field` bounds and `model_validator(mode="after")` checks below. Each message names its field(s), so the `ConfigError` text contains them.

Bounds (`Field(gt/ge/lt/le)`): `max_gross` (0, 5] · `net_min`, `net_max` [−1, 1] · `beta_min`, `beta_max` [−2, 2] · `max_single_long`, `max_single_short`, `max_sector_net`, `max_order_adv_frac`, `liquidation_adv_frac` (0, 1] · `target_vol_annual`, `max_var`, `max_cvar`, `max_stress_loss` (0, 1) · `var_confidence`, `cvar_confidence` (0.5, 1) · `max_style_exposure_sigma` (0, 5] · `adv_window_days` ≥ 1 · `max_liquidation_days` > 0 · `DrawdownTier.drawdown` (0, 1), `degross_frac` [0, 1] · `PodStops` fields (0, 1) · `PodConfig.risk_budget` [0, 1] · LLM budgets > 0 · cost values ≥ 0.

Cross-field checks:
- `LimitsConfig`: `net_min < net_max`; `beta_min < beta_max`; `drawdown_tiers` is non-empty with `drawdown` strictly increasing and `degross_frac` non-decreasing; `pod_stops.halve_budget_at < pod_stops.freeze_at`.
- `UniverseConfig`: exactly one of `index` / `tickers` is set; tickers have no duplicates.
- `BacktestConfig`: `start < in_sample_end`, and `in_sample_end < end` when `end` is set.
- `AtlasConfig`: `math.isclose(math.fsum(risk_budget over all pods), 1.0, abs_tol=1e-9)` (D5). The message is `f"pod risk budgets sum to {total:.6g}, expected 1.0: {per_pod}"`; every `pod.universe` is a key of `data.universes` (message `pods.<id>.universe`).

- [x] **Step 1: Write the failing tests.** Use one table-driven test; each case is an `overrides` dict passed to `load_config(config_dir=write_config(tmp_path, {}))`, plus a regex expected in the `ConfigError`:

```python
INVALID = [
    ({"pods": {"statarb": {"risk_budget": 0.14}}}, r"risk budgets sum to 0\.99"),
    ({"limits": {"net_min": 0.5}}, r"net_min .* net_max"),
    ({"limits": {"beta_min": 0.3}}, r"beta_min .* beta_max"),
    ({"limits": {"drawdown_tiers": [
        {"drawdown": 0.10, "degross_frac": 0.25, "block_new_names": False, "halt": False},
        {"drawdown": 0.05, "degross_frac": 0.50, "block_new_names": True, "halt": True}]}},
     r"drawdown_tiers.*strictly increasing"),
    ({"limits": {"drawdown_tiers": [
        {"drawdown": 0.05, "degross_frac": 0.50, "block_new_names": False, "halt": False},
        {"drawdown": 0.10, "degross_frac": 0.25, "block_new_names": True, "halt": True}]}},
     r"degross_frac.*non-decreasing"),
    ({"limits": {"drawdown_tiers": []}}, r"drawdown_tiers"),
    ({"limits": {"pod_stops": {"halve_budget_at": 0.12, "freeze_at": 0.08}}}, r"halve_budget_at .* freeze_at"),
    ({"pods": {"global_macro": {"universe": "nasdaq"}}}, r"pods\.global_macro\.universe.*nasdaq"),
    ({"data": {"universes": {"x": {"description": "both", "index": "SP500", "tickers": ["SPY"]}}}},
     r"exactly one of index"),
    ({"data": {"universes": {"x": {"description": "dup", "tickers": ["SPY", "SPY"]}}}}, r"duplicate.*SPY"),
    ({"backtest": {"start": "2022-01-01"}}, r"start .* in_sample_end"),
    ({"backtest": {"end": "2020-01-01"}}, r"in_sample_end .* end"),
    ({"limits": {"max_gross": 0}}, r"limits\.max_gross"),
    ({"limits": {"var_confidence": 1.2}}, r"limits\.var_confidence"),
]

@pytest.mark.parametrize(("overrides", "pattern"), INVALID)
def test_invalid_config_raises(tmp_path, overrides, pattern):
    with pytest.raises(ConfigError, match=pattern):
        load_config(overrides=overrides, config_dir=write_config(tmp_path, {}))

def test_percent_instead_of_fraction_rejected(tmp_path):            # Review focus 3
    with pytest.raises(ConfigError, match=r"limits\.max_single_long"):
        load_config(overrides={"limits": {"max_single_long": 5}}, config_dir=write_config(tmp_path, {}))

def test_disabled_pod_keeps_budget_valid(tmp_path):                 # D5
    cfg = load_config(overrides={"pods": {"statarb": {"enabled": False}}}, config_dir=write_config(tmp_path, {}))
    assert cfg.pods["statarb"].enabled is False
```

- [x] **Step 2: Run** `uv run pytest tests/unit/core/test_config.py -q` → the `INVALID` cases and the percent test FAIL (no `ConfigError` raised). `test_disabled_pod_keeps_budget_valid` already passes; it guards D5 against a later "enabled pods only" sum.
- [x] **Step 3: Implement** the bounds and validators. Raise `ValueError` inside the validators so pydantic adds the location.
- [x] **Step 4: Run** the same command → PASS.
- [x] **Step 5: Worklog entry.**

### Task 4 — Env, .env and secrets

**Files:** modify `src/atlas/core/config.py`, `tests/unit/core/test_config.py`

**Interfaces — produces:**
- Env layers come from pydantic-settings: `EnvSettingsSource(AtlasConfig, env_prefix="ATLAS__", env_nested_delimiter="__", case_sensitive=False)()` and `DotEnvSettingsSource(..., env_file=config_dir.parent / ".env")()`. From the `.env` dict, keep only keys in `_SECTIONS = {"fund", "limits", "pods", "data", "llm", "backtest"}`. That drops the unprefixed extras such as `fred_api_key` (Review focus 1).
- Unknown-section guard: scan `os.environ` and the raw `.env` (`dotenv.dotenv_values`) for names that start with `ATLAS__` in any case. If the second segment, lower-cased, is not in `_SECTIONS`, raise `ConfigError` naming the variable and the valid sections. This also rejects `ATLAS__SECRETS__…` and `ATLAS__PROFILE` (D6).
- Reserved keys: top-level `secrets` or `profile` in any YAML root layer (`fund.yaml`, a profile file) or in `overrides` → `ConfigError("'secrets' may only come from environment variables or .env")` / `ConfigError("'profile' may only come from load_config(profile=...)")`, with the file named when it came from YAML.
- Secrets: private `_EnvSecrets(BaseSettings)` with `extra="ignore"`, `case_sensitive=False`, no prefix, and the same five fields. `load_config` builds `Secrets(**_EnvSecrets(_env_file=dotenv_path).model_dump())`, so the process env beats `.env`.

- [x] **Step 1: Write the failing tests:**

```python
def test_env_overrides_nested_key(tmp_path, monkeypatch):
    d = write_config(tmp_path, {"profiles/dev.yaml": "limits: {max_gross: 1.6}\n"})
    monkeypatch.setenv("ATLAS__LIMITS__MAX_GROSS", "1.5")
    monkeypatch.setenv("ATLAS__PODS__STATARB__ENABLED", "false")
    cfg = load_config(config_dir=d)
    assert cfg.limits.max_gross == 1.5 and cfg.pods["statarb"].enabled is False
    assert load_config(overrides={"limits": {"max_gross": 1.2}}, config_dir=d).limits.max_gross == 1.2

def test_env_json_list_value(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS__LIMITS__DRAWDOWN_TIERS",
        '[{"drawdown": 0.06, "degross_frac": 1.0, "block_new_names": true, "halt": true}]')
    assert load_config(config_dir=write_config(tmp_path, {})).limits.drawdown_tiers[0].drawdown == 0.06

def test_dotenv_layer_below_process_env(tmp_path, monkeypatch):
    d = write_config(tmp_path, {})
    (tmp_path / ".env").write_text("ATLAS__LIMITS__MAX_GROSS=1.7\nATLAS__LIMITS__MAX_VAR=0.01\n")
    monkeypatch.setenv("ATLAS__LIMITS__MAX_GROSS", "1.5")
    cfg = load_config(config_dir=d)
    assert (cfg.limits.max_gross, cfg.limits.max_var) == (1.5, 0.01)

def test_dotenv_with_secrets_loads(tmp_path):                       # Review focus 1
    d = write_config(tmp_path, {})
    (tmp_path / ".env").write_text("FRED_API_KEY=fred-123\nSEC_USER_AGENT=Atlas a@b.c\n")
    s = load_config(config_dir=d).secrets
    assert s.fred_api_key.get_secret_value() == "fred-123"
    assert s.sec_user_agent.get_secret_value() == "Atlas a@b.c"

def test_process_env_secret_beats_dotenv(tmp_path, monkeypatch):
    d = write_config(tmp_path, {})
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=from-file\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    assert load_config(config_dir=d).secrets.anthropic_api_key.get_secret_value() == "from-env"

def test_unknown_env_section_raises(tmp_path, monkeypatch):        # Review focus 2
    monkeypatch.setenv("ATLAS__LIMTS__MAX_GROSS", "1.5")
    with pytest.raises(ConfigError, match="ATLAS__LIMTS__MAX_GROSS"):
        load_config(config_dir=write_config(tmp_path, {}))

@pytest.mark.parametrize("var", ["ATLAS__SECRETS__FRED_API_KEY", "ATLAS__PROFILE"])
def test_reserved_env_vars_rejected(tmp_path, monkeypatch, var):
    monkeypatch.setenv(var, "x")
    with pytest.raises(ConfigError, match=var):
        load_config(config_dir=write_config(tmp_path, {}))

@pytest.mark.parametrize("files, overrides", [
    ({"fund.yaml": "secrets: {fred_api_key: leaked}\n"}, None),
    ({"profiles/dev.yaml": "profile: paper\n"}, None),
    ({}, {"secrets": {"fred_api_key": "leaked"}}),
])
def test_reserved_keys_rejected_outside_env(tmp_path, files, overrides):
    with pytest.raises(ConfigError, match="may only come from"):
        load_config(overrides=overrides, config_dir=write_config(tmp_path, files))

def test_secrets_never_in_repr_or_dump(tmp_path, monkeypatch):
    for i, name in enumerate(SECRET_VARS):
        monkeypatch.setenv(name, f"s3cr3t-{i}")
    cfg = load_config(config_dir=write_config(tmp_path, {}))
    for text in (repr(cfg), str(cfg), repr(cfg.secrets), cfg.model_dump_json()):
        assert "s3cr3t" not in text
```

- [x] **Step 2: Run** `uv run pytest tests/unit/core/test_config.py -q` → the new tests FAIL.
- [x] **Step 3: Implement** the env and `.env` layers, the unknown-section guard, the reserved-key check and the secrets loader. Error messages print variable **names** only, never values.
- [x] **Step 4: Run** the same command → PASS.
- [x] **Step 5: Worklog entry.**

### Task 5 — `config_fingerprint`

**Files:** modify `src/atlas/core/config.py`, `tests/unit/core/test_config.py`

**Interfaces — produces:** `config_fingerprint(cfg: AtlasConfig) -> str`. It returns the first 12 hex chars of SHA-256 over `json.dumps(cfg.model_dump(mode="json", exclude={"secrets"}), sort_keys=True, separators=(",", ":"))`. Stage 03 stores it in `RunContext.config_fingerprint`.

- [x] **Step 1: Write the failing tests:**

```python
def test_fingerprint_shape_and_stability():
    fp = config_fingerprint(AtlasConfig())
    assert re.fullmatch(r"[0-9a-f]{12}", fp)
    assert fp == config_fingerprint(AtlasConfig())

def test_fingerprint_changes_with_a_limit():
    changed = AtlasConfig(limits=LimitsConfig(max_gross=1.5))
    assert config_fingerprint(changed) != config_fingerprint(AtlasConfig())

def test_fingerprint_changes_with_profile(tmp_path):
    d = write_config(tmp_path, {})
    assert config_fingerprint(load_config("dev", config_dir=d)) != config_fingerprint(load_config("paper", config_dir=d))

def test_fingerprint_ignores_secrets(tmp_path, monkeypatch):
    d = write_config(tmp_path, {})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key-one")
    a = load_config(config_dir=d)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key-two")
    b = load_config(config_dir=d)
    assert a.secrets != b.secrets
    assert config_fingerprint(a) == config_fingerprint(b)

def test_fingerprint_independent_of_pod_file_order(tmp_path):
    a = AtlasConfig()
    b = AtlasConfig(pods=dict(reversed(list(a.pods.items()))))
    assert config_fingerprint(a) == config_fingerprint(b)

def test_fingerprint_same_across_processes():
    code = "from atlas.core.config import AtlasConfig, config_fingerprint; print(config_fingerprint(AtlasConfig()))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == config_fingerprint(AtlasConfig())
```

- [x] **Step 2: Run** `uv run pytest tests/unit/core/test_config.py -q` → FAIL (`config_fingerprint` not defined).
- [x] **Step 3: Implement** `config_fingerprint`.
- [x] **Step 4: Run** the same command → PASS.
- [x] **Step 5: Worklog entry.**

### Task 6 — Repository YAML files and `.env.example`

**Files:** create `config/fund.yaml`, `config/limits.yaml`, `config/universe.yaml`, `config/pods/{fundamental_ls,systematic,statarb,global_macro,event_driven}.yaml`, `config/profiles/{dev,backtest,paper}.yaml`, `.env.example` · modify `tests/unit/core/test_config.py`

Contents: the base files restate every code default from Task 1 exactly. Write `None` defaults as `null` (`backtest.end`, `macro_etfs.index`) and empty lists as `[]` (`sp500.tickers`), so that every key is present. Each key has an inline or preceding comment giving its meaning and units, its spec source (§8/§9/A6–A9/Stage NN brief), and "provisional, revisit in Stage NN" where D8 applies. The profile files differ from the defaults only here:
- `dev.yaml`: `llm.budgets.daily_usd: 1.0` (cheap local development)
- `backtest.yaml`: restates `backtest.start` / `in_sample_end` (A7) and no other change
- `paper.yaml`: `llm.budgets.daily_usd: 5.0` (A8, live/paper)

`.env.example` lists `ANTHROPIC_API_KEY=`, `ALPACA_API_KEY_ID=`, `ALPACA_API_SECRET_KEY=`, `FRED_API_KEY=`, `SEC_USER_AGENT=` with comments, plus one commented example `# ATLAS__LIMITS__MAX_GROSS=1.5`. Confirm that `.gitignore` already ignores `.env` and keeps `.env.example` (`!.env.example` is present since Stage 01).

- [x] **Step 1: Write the failing tests** (they use the `repo_root` fixture from `tests/conftest.py`):

```python
def test_repo_backtest_profile_fully_populated(repo_root):
    cfg = load_config("backtest", config_dir=repo_root / "config")
    defaults = AtlasConfig()
    assert cfg.profile == "backtest"
    assert (cfg.fund, cfg.limits, cfg.pods, cfg.data, cfg.backtest) == (
        defaults.fund, defaults.limits, defaults.pods, defaults.data, defaults.backtest)
    assert set(cfg.pods) == {"fundamental_ls", "systematic", "statarb", "global_macro", "event_driven"}

@pytest.mark.parametrize("profile", ["dev", "backtest", "paper"])
def test_every_repo_profile_loads(repo_root, profile):
    assert load_config(profile, config_dir=repo_root / "config").profile == profile

def test_every_config_key_is_written_in_yaml(repo_root):
    # dotted paths of AtlasConfig().model_dump(mode="json", exclude={"secrets", "profile"})
    # (dicts recurse, lists are leaves) ⊆ dotted paths of the merged base YAML
    # (fund, limits, universe, pods; no profile), built with _read_yaml/_deep_merge and the D1 mapping
    assert model_paths - yaml_paths == set()

def test_every_yaml_key_has_a_comment(repo_root):
    # every line matching r"^\s*(- )?[A-Za-z_][\w-]*:" in config/**/*.yaml has " #" on the line
    # or a comment on the line above; plain list items ("- SPY") are exempt
    assert uncommented == []

def test_env_example_lists_exactly_the_secrets(repo_root):
    names = {ln.split("=", 1)[0] for ln in (repo_root / ".env.example").read_text().splitlines()
             if ln and not ln.startswith("#")}
    assert names == set(SECRET_VARS) == {f.upper() for f in Secrets.model_fields}
    assert all(ln.endswith("=") for ln in (repo_root / ".env.example").read_text().splitlines()
               if ln and not ln.startswith("#"))
```

- [x] **Step 2: Run** `uv run pytest tests/unit/core/test_config.py -q` → the new tests FAIL (files missing).
- [x] **Step 3: Write** the YAML files and `.env.example`.
- [x] **Step 4: Run** the same command → PASS.
- [x] **Step 5: Worklog entry.**

### Task 7 — Stage verification, records and commit (Definition of Done)

**Files:** `Planning/stages/stage-02-configuration/{worklog.md,verification.md,artifacts/*}`, `Planning/PROGRESS.md`

- [x] **Step 1:** Run each command with full output saved to `artifacts/`, and read each one:
  - `uv run pytest tests/unit/core/test_config.py -q` (the brief's Verify) → all passed · `artifacts/verify-test-config.txt`
  - `uv run pytest -q` → all passed, 0 failed · `artifacts/pytest.txt`
  - `uv run ruff check .` → All checks passed · `uv run ruff format --check .` → already formatted · `uv run mypy src/atlas` → Success · `artifacts/lint-type.txt`
  - `make check` → exit 0 · `artifacts/make-check.txt`
- [x] **Step 2:** Check each acceptance criterion against its evidence:
  - `load_config("backtest")` fully populated → `test_repo_backtest_profile_fully_populated`
  - invalid YAML raises `ConfigError` naming the bad key → `test_invalid_value_names_dotted_key`, `test_unknown_key_names_dotted_key`, `test_malformed_yaml_names_file_and_line`, `test_duplicate_yaml_key_raises`, `test_invalid_config_raises[*]`
  - every limit in §9 is a typed field → `test_limits_cover_spec_section_9[*]`, `test_drawdown_tiers_and_pod_stops_defaults`
  - brief *Tests* list: defaults, layering, each validator, env override, fingerprint stability, secrets never in `repr()` → Tasks 1–5 tests
  - DoD: docstrings state units (read `config.py`); every key is in YAML with a comment and in code with a default → `test_every_config_key_is_written_in_yaml`, `test_every_yaml_key_has_a_comment`
- [x] **Step 3:** Write `verification.md` in the Project.md §1.5 format and set **Result: ALL CRITERIA VERIFIED** only if every box is ticked. Copy the summary into `PROGRESS.md` together with the handoff note (protocol §6). It must tell Stage 03 that `errors.py` exists with `AtlasError` and `ConfigError` only, that `config_fingerprint` is ready for `RunContext`, and that `profile` is the run mode. Tick 02, set it `DONE`, set the current stage to 03 `NOT STARTED`, and set stages done to 2 / 66.
- [x] **Step 4:** Commit everything in one commit: `git add -A && git commit -m "stage-02: Configuration system & fund mandate"` (with the attribution trailer). Check that `git status` is clean and `git log --oneline -1` shows the commit.
