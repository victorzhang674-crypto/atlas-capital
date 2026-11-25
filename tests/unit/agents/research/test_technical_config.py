"""Stage 30 Task 2: the technical analyst's settings (`agents.technical`)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from atlas.core.config import AtlasConfig, TechnicalAnalystConfig, load_config

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_defaults() -> None:
    settings = AtlasConfig().agents.technical
    assert settings == TechnicalAnalystConfig()
    assert settings.ohlc_sessions == 10
    assert settings.range_sessions == 252
    assert settings.swing_sessions == (20, 63)
    assert settings.atr_stop_multiples == (2.0, 3.0)
    assert settings.min_stop_pct == 0.01
    assert settings.max_stop_pct == 0.25


def test_fund_yaml_matches_the_defaults() -> None:
    loaded = load_config("dev", config_dir=REPO_ROOT / "config").agents
    assert loaded.technical == TechnicalAnalystConfig()


def test_lists_are_read_as_tuples() -> None:
    settings = TechnicalAnalystConfig.model_validate(
        {"swing_sessions": [10, 40], "atr_stop_multiples": [1.5]}
    )
    assert settings.swing_sessions == (10, 40)
    assert settings.atr_stop_multiples == (1.5,)


@pytest.mark.parametrize(
    "bad",
    [
        {"ohlc_sessions": 0},
        {"ohlc_sessions": 64},
        {"range_sessions": 1},
        {"swing_sessions": []},
        {"swing_sessions": [0]},
        {"swing_sessions": [63, 20]},
        {"swing_sessions": [20, 20]},
        {"swing_sessions": [20, 300]},
        {"atr_stop_multiples": []},
        {"atr_stop_multiples": [0.0]},
        {"atr_stop_multiples": [3.0, 2.0]},
        {"atr_stop_multiples": [2.0, 2.0]},
        {"min_stop_pct": 0.0},
        {"max_stop_pct": 1.0},
        {"min_stop_pct": 0.2, "max_stop_pct": 0.2},
        {"min_stop_pct": 0.3, "max_stop_pct": 0.2},
        {"min_stop_pct": True},
        {"unknown_key": 1},
    ],
)
def test_bad_settings_are_refused(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TechnicalAnalystConfig.model_validate(bad)
