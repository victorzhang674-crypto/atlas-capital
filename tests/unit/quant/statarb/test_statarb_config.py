"""Stage 23 Task 0: the `quant.statarb` configuration (defaults = config/fund.yaml, validators)."""

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from atlas.core.config import AtlasConfig, StatArbConfig, load_config

REPO = Path(__file__).resolve().parents[4]


def test_defaults() -> None:
    s = AtlasConfig().quant.statarb
    assert s == StatArbConfig()
    assert s.model_dump() == {
        "universe": "sp500",
        "corr_window": 252,
        "formation": 504,
        "min_corr": 0.7,
        "max_pvalue": 0.05,
        "min_half_life": 2.0,
        "max_half_life": 60.0,
        "max_hurst": 0.5,
        "hurst_max_lag": 50,
        "max_pairs_per_name": 2,
        "max_pairs": 40,
        "entry_z": 2.0,
        "exit_z": 0.5,
        "stop_z": 4.0,
        "lookback_mult": 2.0,
        "min_lookback": 5,
        "time_stop_mult": 3.0,
        "breakdown_pvalue": 0.2,
        "breakdown_every": 5,
        "pair_gross": 0.05,
        "cost_bps": 5.0,
    }


def test_repo_yaml_equals_defaults(tmp_path: Path) -> None:
    target = tmp_path / "config"
    shutil.copytree(REPO / "config", target)
    assert load_config("dev", config_dir=target).quant.statarb == StatArbConfig()


@pytest.mark.parametrize(
    ("fields", "match"),
    [
        ({"exit_z": 2.0}, "exit_z"),
        ({"entry_z": 4.0}, "entry_z"),
        ({"entry_z": 5.0}, "entry_z"),
        ({"min_half_life": 60.0}, "min_half_life"),
        ({"corr_window": 600}, "corr_window"),
        ({"corr_window": 504}, "corr_window"),
        ({"hurst_max_lag": 252}, "hurst_max_lag"),
        ({"max_pvalue": 0.3}, "breakdown_pvalue"),
    ],
)
def test_cross_field_validators(fields: dict[str, float], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        StatArbConfig(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "fields",
    [
        {"min_corr": 0.0},
        {"min_corr": 1.0},
        {"max_pvalue": 0.0},
        {"max_hurst": 0.0},
        {"max_hurst": 1.0},
        {"min_half_life": 0.0},
        {"max_pairs": 0},
        {"max_pairs_per_name": 0},
        {"pair_gross": 0.0},
        {"cost_bps": -1.0},
        {"exit_z": -0.1},
        {"exit_z": 0.0},
        {"lookback_mult": 0.0},
        {"min_lookback": 2},
        {"time_stop_mult": 0.0},
        {"breakdown_every": 0},
        {"hurst_max_lag": 2},
        {"corr_window": 10},
        {"universe": ""},
    ],
)
def test_field_validators(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        StatArbConfig(**fields)  # type: ignore[arg-type]
