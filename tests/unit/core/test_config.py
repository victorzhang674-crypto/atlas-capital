"""Tests for `atlas.core.config`: defaults, layering, validation, env/secrets, fingerprint."""

import copy
import os
import pickle
import re
import shutil
import subprocess
import sys
import traceback
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from atlas.core.config import (
    AtlasConfig,
    FundConfig,
    LimitsConfig,
    PodStops,
    Secrets,
    _deep_merge,
    _yaml_layers,
    config_fingerprint,
    load_config,
)
from atlas.core.errors import AtlasError, ConfigError

SECRET_VARS = (
    "ANTHROPIC_API_KEY",
    "ALPACA_API_KEY_ID",
    "ALPACA_API_SECRET_KEY",
    "FRED_API_KEY",
    "SEC_USER_AGENT",
)

MACRO_ETFS = [
    "SPY",
    "QQQ",
    "IWM",
    "EFA",
    "EEM",
    "XLB",
    "XLC",
    "XLE",
    "XLF",
    "XLI",
    "XLK",
    "XLP",
    "XLRE",
    "XLU",
    "XLV",
    "XLY",
    "TLT",
    "IEF",
    "SHY",
    "LQD",
    "HYG",
    "GLD",
    "SLV",
    "USO",
    "DBC",
    "UUP",
    "FXE",
    "FXY",
    "VNQ",
    "BIL",
]


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the developer's real environment and `.env` out of every test."""
    for name in list(os.environ):
        if name.upper().startswith("ATLAS__") or name.upper() in SECRET_VARS:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


def write_config(root: Path, files: dict[str, str]) -> Path:
    """Write {relative path: YAML text} under root/'config'; return that config dir."""
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        path = config_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return config_dir


# --- Task 1: models with defaults -------------------------------------------------------


def test_config_error_is_atlas_error() -> None:
    assert issubclass(ConfigError, AtlasError)


def test_defaults_without_files() -> None:
    cfg = AtlasConfig()
    assert cfg.profile == "dev"
    assert cfg.fund == FundConfig(
        name="Atlas Capital",
        base_currency="USD",
        initial_capital=Decimal("10000000"),
        benchmark="SPY",
    )
    assert cfg.llm.tiers.deep == "claude-opus-5-5"
    assert cfg.llm.tiers.quick == "claude-haiku-5-5"
    assert cfg.llm.budgets.daily_usd == 5.0
    assert cfg.backtest.start == date(2018, 1, 1)
    assert cfg.backtest.in_sample_end == date(2021, 12, 31)
    assert cfg.backtest.end is None
    assert cfg.backtest.costs.impact_k == 0.1
    assert cfg.secrets.anthropic_api_key is None


SPEC_9 = {
    "target_vol_annual": 0.10,
    "max_gross": 2.0,
    "net_min": -0.10,
    "net_max": 0.40,
    "beta_min": -0.10,
    "beta_max": 0.30,
    "max_single_long": 0.05,
    "max_single_short": 0.03,
    "max_sector_net": 0.15,
    "max_style_exposure_sigma": 0.5,
    "max_order_adv_frac": 0.05,
    "adv_window_days": 20,
    "max_liquidation_days": 3.0,
    "liquidation_adv_frac": 0.10,
    "var_confidence": 0.95,
    "max_var": 0.015,
    "cvar_confidence": 0.975,
    "max_cvar": 0.025,
    "max_stress_loss": 0.15,
}


@pytest.mark.parametrize(("field", "value"), SPEC_9.items())
def test_limits_cover_spec_section_9(field: str, value: float) -> None:
    assert getattr(LimitsConfig(), field) == value
    expected_type = int if field == "adv_window_days" else float
    assert LimitsConfig.model_fields[field].annotation is expected_type


def test_drawdown_tiers_and_pod_stops_defaults() -> None:
    limits = LimitsConfig()
    tiers = [(t.drawdown, t.degross_frac, t.block_new_names, t.halt) for t in limits.drawdown_tiers]
    assert tiers == [(0.05, 0.25, False, False), (0.10, 0.50, True, False), (0.15, 1.0, True, True)]
    assert limits.pod_stops == PodStops(halve_budget_at=0.08, budget_multiplier=0.5, freeze_at=0.12)


def test_default_pods_match_spec_section_8() -> None:
    pods = AtlasConfig().pods
    assert {k: (p.risk_budget, p.rebalance, p.universe) for k, p in pods.items()} == {
        "fundamental_ls": (0.30, "weekly", "sp500"),
        "systematic": (0.25, "weekly", "sp500"),
        "statarb": (0.15, "daily", "sp500"),
        "global_macro": (0.20, "weekly", "macro_etfs"),
        "event_driven": (0.10, "event", "sp500"),
    }
    assert all(p.enabled for p in pods.values())


def test_default_macro_universe_is_stage_08_list() -> None:
    assert len(MACRO_ETFS) == 30
    assert AtlasConfig().data.universes["macro_etfs"].tickers == tuple(MACRO_ETFS)


def test_config_is_frozen() -> None:
    limits = AtlasConfig().limits
    with pytest.raises(ValidationError):
        limits.max_gross = 1.0  # type: ignore[misc]


# --- Task 2: YAML loader and merge order ------------------------------------------------


def test_load_config_empty_dir_equals_defaults(tmp_path: Path) -> None:
    assert load_config(config_dir=write_config(tmp_path, {})) == AtlasConfig()


def test_missing_config_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="config directory"):
        load_config(config_dir=tmp_path / "nope")


def test_unknown_profile_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"'live'.*dev, backtest, paper"):
        load_config("live", config_dir=write_config(tmp_path, {}))


def test_default_config_dir_is_cwd_config(tmp_path: Path) -> None:
    # The autouse fixture has already chdir'd to tmp_path.
    write_config(tmp_path, {"limits.yaml": "max_gross: 1.8\n"})
    assert load_config().limits.max_gross == 1.8


def test_layer_order(tmp_path: Path) -> None:
    d = write_config(
        tmp_path,
        {
            "fund.yaml": "fund: {benchmark: QQQ}\nllm: {budgets: {daily_usd: 4.0}}\n",
            "limits.yaml": "max_gross: 1.8\nmax_var: 0.012\n",
            "profiles/backtest.yaml": (
                "limits: {max_gross: 1.6}\nllm: {budgets: {daily_usd: 3.0}}\n"
                "pods: {statarb: {rebalance: weekly}}\n"
            ),
        },
    )
    cfg = load_config("backtest", overrides={"limits": {"max_gross": 1.4}}, config_dir=d)
    assert cfg.profile == "backtest"
    assert cfg.fund.benchmark == "QQQ"  # base file beats default
    assert cfg.limits.max_var == 0.012  # untouched by later layers
    assert cfg.pods["statarb"].rebalance == "weekly"
    assert cfg.pods["statarb"].risk_budget == 0.15  # a partial pod override merges
    assert cfg.llm.budgets.daily_usd == 3.0  # profile beats base
    assert cfg.limits.max_gross == 1.4  # override beats profile
    assert load_config("dev", config_dir=d).limits.max_gross == 1.8  # other profile not read


def test_lists_are_replaced_not_merged(tmp_path: Path) -> None:
    d = write_config(
        tmp_path,
        {
            "profiles/dev.yaml": (
                "limits:\n  drawdown_tiers:\n"
                "    - {drawdown: 0.04, degross_frac: 0.5, block_new_names: true, halt: true}\n"
            )
        },
    )
    assert len(load_config(config_dir=d).limits.drawdown_tiers) == 1


def test_invalid_value_names_dotted_key(tmp_path: Path) -> None:
    d = write_config(tmp_path, {"pods/statarb.yaml": "risk_budget: lots\n"})
    with pytest.raises(ConfigError, match=r"pods\.statarb\.risk_budget"):
        load_config(config_dir=d)


def test_unknown_key_names_dotted_key(tmp_path: Path) -> None:
    d = write_config(tmp_path, {"limits.yaml": "max_gros: 1.5\n"})
    with pytest.raises(ConfigError, match=r"limits\.max_gros"):
        load_config(config_dir=d)


def test_malformed_yaml_names_file_and_line(tmp_path: Path) -> None:
    d = write_config(tmp_path, {"limits.yaml": "max_gross: 1.5\nnet_min: [oops\n"})
    with pytest.raises(ConfigError, match=r"limits\.yaml.*line \d+"):
        load_config(config_dir=d)


def test_duplicate_yaml_key_raises(tmp_path: Path) -> None:  # Review focus 4
    d = write_config(tmp_path, {"limits.yaml": "max_gross: 1.5\nmax_gross: 3.0\n"})
    with pytest.raises(ConfigError, match=r"limits\.yaml.*duplicate key 'max_gross'"):
        load_config(config_dir=d)


def test_empty_yaml_is_no_layer(tmp_path: Path) -> None:  # Review focus 5
    d = write_config(tmp_path, {"limits.yaml": "", "profiles/dev.yaml": "# only a comment\n"})
    assert load_config(config_dir=d) == AtlasConfig()


def test_non_mapping_yaml_raises(tmp_path: Path) -> None:  # Review focus 5
    d = write_config(tmp_path, {"limits.yaml": "- 1\n- 2\n"})
    with pytest.raises(ConfigError, match=r"limits\.yaml.*mapping"):
        load_config(config_dir=d)


# --- Task 3: validators -----------------------------------------------------------------


def _tiers(*rows: tuple[float, float, bool, bool]) -> list[dict[str, object]]:
    keys = ("drawdown", "degross_frac", "block_new_names", "halt")
    return [dict(zip(keys, row, strict=True)) for row in rows]


INVALID = [
    ({"pods": {"statarb": {"risk_budget": 0.14}}}, r"risk budgets sum to 0\.99"),
    ({"limits": {"net_min": 0.5}}, r"net_min .* net_max"),
    ({"limits": {"beta_min": 0.3}}, r"beta_min .* beta_max"),
    (
        {"limits": {"drawdown_tiers": _tiers((0.10, 0.25, False, False), (0.05, 0.5, True, True))}},
        r"drawdown_tiers.*strictly increasing",
    ),
    (
        {"limits": {"drawdown_tiers": _tiers((0.05, 0.5, False, False), (0.10, 0.25, True, True))}},
        r"degross_frac.*non-decreasing",
    ),
    ({"limits": {"drawdown_tiers": []}}, r"drawdown_tiers"),
    (
        {"limits": {"pod_stops": {"halve_budget_at": 0.12, "freeze_at": 0.08}}},
        r"halve_budget_at .* freeze_at",
    ),
    ({"pods": {"global_macro": {"universe": "nasdaq"}}}, r"pods\.global_macro\.universe.*nasdaq"),
    (
        {
            "data": {
                "universes": {"x": {"description": "both", "index": "SP500", "tickers": ["SPY"]}}
            }
        },
        r"exactly one of index",
    ),
    (
        {"data": {"universes": {"x": {"description": "dup", "tickers": ["SPY", "SPY"]}}}},
        r"duplicate.*SPY",
    ),
    ({"backtest": {"start": "2022-01-01"}}, r"start .* in_sample_end"),
    ({"backtest": {"end": "2020-01-01"}}, r"in_sample_end .* end"),
    ({"limits": {"max_gross": 0}}, r"limits\.max_gross"),
    ({"limits": {"var_confidence": 1.2}}, r"limits\.var_confidence"),
]


@pytest.mark.parametrize(("overrides", "pattern"), INVALID)
def test_invalid_config_raises(tmp_path: Path, overrides: dict[str, object], pattern: str) -> None:
    with pytest.raises(ConfigError, match=pattern):
        load_config(overrides=overrides, config_dir=write_config(tmp_path, {}))


def test_percent_instead_of_fraction_rejected(tmp_path: Path) -> None:  # Review focus 3
    with pytest.raises(ConfigError, match=r"limits\.max_single_long"):
        load_config(
            overrides={"limits": {"max_single_long": 5}}, config_dir=write_config(tmp_path, {})
        )


def test_disabled_pod_keeps_budget_valid(tmp_path: Path) -> None:  # D5
    cfg = load_config(
        overrides={"pods": {"statarb": {"enabled": False}}}, config_dir=write_config(tmp_path, {})
    )
    assert cfg.pods["statarb"].enabled is False


# --- Task 4: env, .env and secrets ------------------------------------------------------


def _secret(value: SecretStr | None) -> str:
    assert value is not None
    return value.get_secret_value()


def test_env_overrides_nested_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = write_config(tmp_path, {"profiles/dev.yaml": "limits: {max_gross: 1.6}\n"})
    monkeypatch.setenv("ATLAS__LIMITS__MAX_GROSS", "1.5")
    monkeypatch.setenv("ATLAS__PODS__STATARB__ENABLED", "false")
    cfg = load_config(config_dir=d)
    assert cfg.limits.max_gross == 1.5  # env beats profile
    assert cfg.pods["statarb"].enabled is False
    overridden = load_config(overrides={"limits": {"max_gross": 1.2}}, config_dir=d)
    assert overridden.limits.max_gross == 1.2  # explicit override beats env


def test_env_json_list_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "ATLAS__LIMITS__DRAWDOWN_TIERS",
        '[{"drawdown": 0.06, "degross_frac": 1.0, "block_new_names": true, "halt": true}]',
    )
    tiers = load_config(config_dir=write_config(tmp_path, {})).limits.drawdown_tiers
    assert [t.drawdown for t in tiers] == [0.06]


def test_dotenv_layer_below_process_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = write_config(tmp_path, {})
    (tmp_path / ".env").write_text("ATLAS__LIMITS__MAX_GROSS=1.7\nATLAS__LIMITS__MAX_VAR=0.01\n")
    monkeypatch.setenv("ATLAS__LIMITS__MAX_GROSS", "1.5")
    cfg = load_config(config_dir=d)
    assert (cfg.limits.max_gross, cfg.limits.max_var) == (1.5, 0.01)


def test_dotenv_with_secrets_loads(tmp_path: Path) -> None:  # Review focus 1
    d = write_config(tmp_path, {})
    (tmp_path / ".env").write_text("FRED_API_KEY=fred-123\nSEC_USER_AGENT=Atlas a@b.c\n")
    secrets = load_config(config_dir=d).secrets
    assert _secret(secrets.fred_api_key) == "fred-123"
    assert _secret(secrets.sec_user_agent) == "Atlas a@b.c"


def test_process_env_secret_beats_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = write_config(tmp_path, {})
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=from-file\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    assert _secret(load_config(config_dir=d).secrets.anthropic_api_key) == "from-env"


def test_unknown_env_section_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Review focus 2
    monkeypatch.setenv("ATLAS__LIMTS__MAX_GROSS", "1.5")
    with pytest.raises(ConfigError, match="ATLAS__LIMTS__MAX_GROSS"):
        load_config(config_dir=write_config(tmp_path, {}))


def test_unknown_dotenv_section_raises(tmp_path: Path) -> None:
    d = write_config(tmp_path, {})
    (tmp_path / ".env").write_text("ATLAS__LIMTS__MAX_GROSS=1.5\n")
    with pytest.raises(ConfigError, match="ATLAS__LIMTS__MAX_GROSS"):
        load_config(config_dir=d)


@pytest.mark.parametrize("var", ["ATLAS__SECRETS__FRED_API_KEY", "ATLAS__PROFILE"])
def test_reserved_env_vars_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, var: str
) -> None:
    monkeypatch.setenv(var, "x")
    with pytest.raises(ConfigError, match=var):
        load_config(config_dir=write_config(tmp_path, {}))


@pytest.mark.parametrize(
    ("files", "overrides"),
    [
        ({"fund.yaml": "secrets: {fred_api_key: leaked}\n"}, None),
        ({"profiles/dev.yaml": "profile: paper\n"}, None),
        ({}, {"secrets": {"fred_api_key": "leaked"}}),
    ],
)
def test_reserved_keys_rejected_outside_env(
    tmp_path: Path, files: dict[str, str], overrides: dict[str, object] | None
) -> None:
    with pytest.raises(ConfigError, match="may only come from"):
        load_config(overrides=overrides, config_dir=write_config(tmp_path, files))


def test_secrets_never_in_repr_or_dump(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for i, name in enumerate(SECRET_VARS):
        monkeypatch.setenv(name, f"s3cr3t-{i}")
    cfg = load_config(config_dir=write_config(tmp_path, {}))
    assert _secret(cfg.secrets.alpaca_api_secret_key) == "s3cr3t-2"
    for text in (repr(cfg), str(cfg), repr(cfg.secrets), cfg.model_dump_json()):
        assert "s3cr3t" not in text


# --- Task 5: config_fingerprint ---------------------------------------------------------


def test_fingerprint_shape_and_stability() -> None:
    fp = config_fingerprint(AtlasConfig())
    assert re.fullmatch(r"[0-9a-f]{12}", fp)
    assert fp == config_fingerprint(AtlasConfig())


def test_fingerprint_changes_with_a_limit() -> None:
    changed = AtlasConfig(limits=LimitsConfig(max_gross=1.5))
    assert config_fingerprint(changed) != config_fingerprint(AtlasConfig())


def test_fingerprint_changes_with_profile(tmp_path: Path) -> None:
    d = write_config(tmp_path, {})
    dev = load_config("dev", config_dir=d)
    paper = load_config("paper", config_dir=d)
    assert config_fingerprint(dev) != config_fingerprint(paper)


def test_fingerprint_ignores_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = write_config(tmp_path, {})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key-one")
    a = load_config(config_dir=d)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key-two")
    b = load_config(config_dir=d)
    assert a.secrets != b.secrets
    assert config_fingerprint(a) == config_fingerprint(b)


def test_fingerprint_independent_of_pod_order() -> None:
    a = AtlasConfig()
    b = AtlasConfig(pods=dict(reversed(list(a.pods.items()))))
    assert list(a.pods) != list(b.pods)
    assert config_fingerprint(a) == config_fingerprint(b)


def test_fingerprint_same_across_processes() -> None:
    code = (
        "from atlas.core.config import AtlasConfig, config_fingerprint; "
        "print(config_fingerprint(AtlasConfig()))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == config_fingerprint(AtlasConfig())


# --- Task 6: repository YAML files and .env.example ------------------------------------


def _repo_config_copy(repo_root: Path, tmp_path: Path) -> Path:
    """Copy the repo's config/ into tmp_path, so loading never reads the repo's real .env."""
    target = tmp_path / "repo" / "config"
    shutil.copytree(repo_root / "config", target)
    return target


def _dotted_paths(tree: dict[str, object], prefix: str = "") -> set[str]:
    """Every key path in a nested dict; lists are leaves."""
    paths: set[str] = set()
    for key, value in tree.items():
        path = f"{prefix}{key}"
        paths.add(path)
        if isinstance(value, dict):
            paths |= _dotted_paths(value, f"{path}.")
    return paths


def test_repo_backtest_profile_fully_populated(repo_root: Path, tmp_path: Path) -> None:
    cfg = load_config("backtest", config_dir=_repo_config_copy(repo_root, tmp_path))
    defaults = AtlasConfig()
    assert cfg.profile == "backtest"
    assert (cfg.fund, cfg.limits, cfg.pods, cfg.data, cfg.backtest) == (
        defaults.fund,
        defaults.limits,
        defaults.pods,
        defaults.data,
        defaults.backtest,
    )
    assert set(cfg.pods) == {
        "fundamental_ls",
        "systematic",
        "statarb",
        "global_macro",
        "event_driven",
    }


@pytest.mark.parametrize("profile", ["dev", "backtest", "paper"])
def test_every_repo_profile_loads(repo_root: Path, tmp_path: Path, profile: str) -> None:
    cfg = load_config(profile, config_dir=_repo_config_copy(repo_root, tmp_path))
    assert cfg.profile == profile


def test_repo_profile_budgets(repo_root: Path, tmp_path: Path) -> None:
    d = _repo_config_copy(repo_root, tmp_path)
    assert load_config("dev", config_dir=d).llm.budgets.daily_usd == 1.0
    assert load_config("paper", config_dir=d).llm.budgets.daily_usd == 5.0


def test_every_config_key_is_written_in_yaml(repo_root: Path) -> None:
    dump = AtlasConfig().model_dump(mode="json", exclude={"secrets", "profile"})
    base_layers = _yaml_layers(repo_root / "config", "dev")[:-1]  # every layer but the profile
    model_paths = _dotted_paths(dump)
    yaml_paths = _dotted_paths(_deep_merge(*base_layers))
    assert model_paths - yaml_paths == set()


KEY_LINE = re.compile(r"^\s*(- )?[A-Za-z_][\w-]*:")


def test_every_yaml_key_has_a_comment(repo_root: Path) -> None:
    uncommented = []
    for path in sorted((repo_root / "config").rglob("*.yaml")):
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            commented_above = i > 0 and lines[i - 1].strip().startswith("#")
            if KEY_LINE.match(line) and " #" not in line and not commented_above:
                uncommented.append(f"{path.relative_to(repo_root)}:{i + 1}: {line.strip()}")
    assert uncommented == []


def test_env_example_lists_exactly_the_secrets(repo_root: Path) -> None:
    lines = [
        ln
        for ln in (repo_root / ".env.example").read_text().splitlines()
        if ln and not ln.startswith("#")
    ]
    names = {ln.split("=", 1)[0] for ln in lines}
    assert names == set(SECRET_VARS) == {field.upper() for field in Secrets.model_fields}
    assert all(ln.endswith("=") for ln in lines)


# --- Final review fixes -----------------------------------------------------------------

FULL_POD = "name: {name}\nrisk_budget: {budget}\nrebalance: weekly\nuniverse: sp500\n"


def test_collections_are_immutable() -> None:
    cfg = AtlasConfig()
    mutations = [
        lambda: cfg.pods.pop("statarb"),  # type: ignore[attr-defined]
        lambda: cfg.limits.drawdown_tiers.clear(),  # type: ignore[attr-defined]
        lambda: cfg.data.universes["macro_etfs"].tickers.append("GME"),  # type: ignore[attr-defined]
        lambda: cfg.data.universes.pop("sp500"),  # type: ignore[attr-defined]
    ]
    for mutate in mutations:
        with pytest.raises((TypeError, AttributeError)):
            mutate()
    assert config_fingerprint(cfg) == config_fingerprint(AtlasConfig())


def test_config_survives_pickle_and_deepcopy() -> None:
    cfg = AtlasConfig()
    assert pickle.loads(pickle.dumps(cfg)) == cfg
    assert copy.deepcopy(cfg) == cfg


def test_bad_json_env_value_raises_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATLAS__LIMITS__DRAWDOWN_TIERS", "notjson")
    with pytest.raises(ConfigError, match=r"environment.*ATLAS__LIMITS"):
        load_config(config_dir=write_config(tmp_path, {}))


def test_bad_json_dotenv_value_raises_config_error(tmp_path: Path) -> None:
    d = write_config(tmp_path, {})
    (tmp_path / ".env").write_text("ATLAS__FUND=notjson\n")
    with pytest.raises(ConfigError, match=r"\.env.*ATLAS__FUND"):
        load_config(config_dir=d)


def test_pod_files_define_the_pod_set(tmp_path: Path) -> None:
    d = write_config(
        tmp_path,
        {
            "pods/alpha.yaml": FULL_POD.format(name="Alpha", budget=0.6),
            "pods/beta.yaml": FULL_POD.format(name="Beta", budget=0.4),
            "profiles/dev.yaml": "pods: {alpha: {enabled: false}}\n",
        },
    )
    cfg = load_config(config_dir=d)
    assert set(cfg.pods) == {"alpha", "beta"}  # the five default pods are gone
    assert cfg.pods["alpha"].enabled is False  # later layers still merge into a pod


def test_pod_file_is_a_full_definition(tmp_path: Path) -> None:
    d = write_config(tmp_path, {"pods/statarb.yaml": "rebalance: weekly\n"})
    with pytest.raises(ConfigError, match=r"pods\.statarb\.risk_budget"):
        load_config(config_dir=d)


def test_universe_file_replaces_default_universes(tmp_path: Path) -> None:
    d = write_config(
        tmp_path,
        {
            "universe.yaml": (
                "sp500: {description: two names, tickers: [AAPL, MSFT]}\n"
                "macro_etfs: {description: an index now, index: R1000}\n"
            )
        },
    )
    universes = load_config(config_dir=d).data.universes
    assert (universes["sp500"].index, universes["sp500"].tickers) == (None, ("AAPL", "MSFT"))
    assert (universes["macro_etfs"].index, universes["macro_etfs"].tickers) == ("R1000", ())


def test_yml_files_are_rejected(tmp_path: Path) -> None:
    d = write_config(tmp_path, {"pods/new_pod.yml": FULL_POD.format(name="New", budget=0.1)})
    with pytest.raises(ConfigError, match=r"new_pod\.yml.*\.yaml"):
        load_config(config_dir=d)


def test_non_utf8_yaml_raises_config_error(tmp_path: Path) -> None:
    d = write_config(tmp_path, {})
    (d / "limits.yaml").write_bytes(b"max_gross: \xff\xfe\n")
    with pytest.raises(ConfigError, match=r"limits\.yaml.*UTF-8"):
        load_config(config_dir=d)


def test_unreadable_yaml_raises_config_error(tmp_path: Path) -> None:
    d = write_config(tmp_path, {"limits.yaml": "max_gross: 1.5\n"})
    (d / "limits.yaml").chmod(0)
    try:
        with pytest.raises(ConfigError, match=r"limits\.yaml.*cannot read"):
            load_config(config_dir=d)
    finally:
        (d / "limits.yaml").chmod(0o644)


def test_unhashable_yaml_key_raises_config_error(tmp_path: Path) -> None:
    d = write_config(tmp_path, {"limits.yaml": "? [a, b]\n: 1\n"})
    with pytest.raises(ConfigError, match=r"limits\.yaml"):
        load_config(config_dir=d)


def test_secret_typed_into_yaml_never_reaches_traceback(tmp_path: Path) -> None:
    d = write_config(tmp_path, {"fund.yaml": "fund: {fred_api_key: REAL-KEY-123}\n"})
    with pytest.raises(ConfigError) as info:
        load_config(config_dir=d)
    assert "fund.fred_api_key" in str(info.value)
    assert "REAL-KEY-123" not in "".join(traceback.format_exception(info.value))
