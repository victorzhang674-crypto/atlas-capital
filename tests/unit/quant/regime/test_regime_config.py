"""Stage 21 Task 0: the `quant.regime` configuration (defaults = config/fund.yaml, validators)."""

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from atlas.core.config import (
    AtlasConfig,
    RegimeConfig,
    RegimeCreditConfig,
    RegimeHMMConfig,
    RegimeMacroConfig,
    RegimeScalarConfig,
    RegimeStressConfig,
    RegimeTrendConfig,
    RegimeVolConfig,
    load_config,
)

REPO = Path(__file__).resolve().parents[4]


def test_defaults() -> None:
    r = AtlasConfig().quant.regime
    assert r == RegimeConfig()
    assert r.model_dump() == {
        "universe": "sp500",
        "train_start": date(2015, 1, 1),
        "max_fill_sessions": 5,
        "vol": {
            "percentile_window": 1260, "percentile_min_obs": 252, "low_below": 0.25,
            "high_from": 0.75, "crisis_from": 0.95, "crisis_vix_floor": 30.0,
            "crisis_rv_floor": 0.30,
        },
        "trend": {
            "breadth_min_names": 20, "breadth_bull_above": 0.5, "up_share": 1.0,
            "down_share": 0.34,
        },
        "hmm": {
            "n_states": 3, "min_obs": 504, "n_init": 5, "n_iter": 200, "tol": 1.0e-4,
            "seed": 21, "turbulent_prob": 0.5, "min_state_share": 0.01,
        },
        "credit": {
            "source": "auto", "oas_series": "BAMLH0A0HYM2", "proxy_long": "HYG",
            "proxy_hedge": "IEF", "proxy_hedge_ratio": 0.5, "zscore_window": 252,
            "zscore_threshold": 2.0,
        },
        "stress": {"nfci_threshold": 0.0},
        "macro": {"claims_short_weeks": 4, "claims_long_weeks": 26, "lookback_days": 800},
        "risk_scalar": {
            "floor": 0.25, "cap": 1.0,
            "vol": {"low": 1.0, "normal": 1.0, "high": 0.7, "crisis": 0.4},
            "trend": {"up": 1.0, "neutral": 0.9, "down": 0.75},
            "stress": 0.6, "hmm_turbulent": 0.85,
            "quadrant": {
                "goldilocks": 1.0, "reflation": 1.0, "stagflation": 0.85, "deflation": 0.9,
            },
        },
    }  # fmt: skip


def test_repo_yaml_equals_defaults(tmp_path: Path) -> None:
    import shutil

    target = tmp_path / "config"
    shutil.copytree(REPO / "config", target)
    assert load_config("dev", config_dir=target).quant.regime == RegimeConfig()


@pytest.mark.parametrize(
    ("model", "kwargs", "match"),
    [
        (RegimeVolConfig, {"percentile_window": 10}, "percentile_window"),
        (RegimeVolConfig, {"percentile_min_obs": 1}, "percentile_min_obs"),
        (RegimeVolConfig, {"percentile_window": 300, "percentile_min_obs": 400}, "min_obs"),
        (RegimeVolConfig, {"low_below": 0.8}, "ascending"),
        (RegimeVolConfig, {"crisis_from": 0.7}, "ascending"),
        (RegimeVolConfig, {"crisis_from": 1.2}, "crisis_from"),
        (RegimeVolConfig, {"crisis_vix_floor": -1.0}, "crisis_vix_floor"),
        (RegimeTrendConfig, {"breadth_min_names": 0}, "breadth_min_names"),
        (RegimeTrendConfig, {"down_share": 1.0}, "down_share"),
        (RegimeTrendConfig, {"up_share": 0.2}, "up_share"),
        (RegimeHMMConfig, {"n_states": 1}, "n_states"),
        (RegimeHMMConfig, {"n_states": 4}, "n_states"),
        (RegimeHMMConfig, {"min_obs": 10}, "min_obs"),
        (RegimeHMMConfig, {"n_init": 0}, "n_init"),
        (RegimeHMMConfig, {"turbulent_prob": 1.0}, "turbulent_prob"),
        (RegimeHMMConfig, {"min_state_share": 0.5}, "min_state_share"),
        (RegimeCreditConfig, {"source": "both"}, "source"),
        (RegimeCreditConfig, {"proxy_hedge_ratio": -0.1}, "proxy_hedge_ratio"),
        (RegimeCreditConfig, {"zscore_window": 5}, "zscore_window"),
        (RegimeCreditConfig, {"zscore_threshold": 0.0}, "zscore_threshold"),
        (RegimeStressConfig, {"nfci_threshold": float("nan")}, "nfci_threshold"),
        (RegimeMacroConfig, {"claims_short_weeks": 30}, "claims"),
        (RegimeMacroConfig, {"lookback_days": 100}, "lookback_days"),
        (RegimeScalarConfig, {"floor": 0.2}, "floor"),
        (RegimeScalarConfig, {"cap": 1.1}, "cap"),
        (RegimeScalarConfig, {"floor": 0.9, "cap": 0.8}, "floor"),
        (RegimeScalarConfig, {"stress": 0.0}, "stress"),
        (RegimeScalarConfig, {"hmm_turbulent": 1.2}, "hmm_turbulent"),
        (RegimeScalarConfig, {"vol": {"normal": 0.6, "high": 0.7}}, "monotone"),
        (RegimeScalarConfig, {"trend": {"up": 0.9, "neutral": 1.0, "down": 0.75}}, "monotone"),
        (RegimeScalarConfig, {"quadrant": {"stagflation": 0.0}}, "stagflation"),
        (RegimeConfig, {"max_fill_sessions": -1}, "max_fill_sessions"),
        (RegimeConfig, {"universe": ""}, "universe"),
    ],
)  # fmt: skip
def test_validation(model: type, kwargs: dict[str, object], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        model(**kwargs)


def test_hmmlearn_is_installed() -> None:
    from hmmlearn.hmm import GaussianHMM

    assert GaussianHMM(n_components=2).n_components == 2
