"""Stage 22 Task 0: the `quant.alpha` configuration (defaults = config/fund.yaml, validators)."""

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from atlas.core.config import (
    AlphaComponentConfig,
    AlphaConfig,
    AlphaMultiFactorConfig,
    AlphaPEADConfig,
    AlphaTSMOMConfig,
    AtlasConfig,
    load_config,
)

REPO = Path(__file__).resolve().parents[4]


def test_defaults() -> None:
    a = AtlasConfig().quant.alpha
    assert a == AlphaConfig()
    assert a.model_dump() == {
        "multifactor": {
            "universe": "sp500",
            "components": {
                "momentum": {
                    "kind": "feature", "source": "momentum_12_1", "sign": 1, "weight": 0.2
                },
                "value": {"kind": "style", "source": "value", "sign": 1, "weight": 0.2},
                "quality": {"kind": "style", "source": "quality", "sign": 1, "weight": 0.2},
                "low_vol": {"kind": "feature", "source": "vol_63d", "sign": -1, "weight": 0.2},
                "reversal": {"kind": "feature", "source": "ret_21d", "sign": -1, "weight": 0.2},
            },
            "min_names": 20,
            "min_coverage": 0.6,
            "weighting": "fixed",
            "horizon": 21,
            "ic_prior": 0.03,
            "ic_window_months": 36,
            "ic_min_obs": 12,
            "prior_confidence": 0.5,
            "t_full": 2.0,
            "resid_vol_feature": "idio_vol_252d",
        },
        "tsmom": {
            "universe": "macro_etfs",
            "lookbacks": (21, 63, 126, 252),
            "min_lookbacks": 2,
            "vol_feature": "vol_63d",
            "vol_target": 0.10,
            "max_position": 2.0,
            "sharpe_prior": 0.3,
            "horizon": 21,
        },
        "pead": {
            "universe": "sp500",
            "decay_sessions": 60,
            "drift_ann": 0.06,
            "car_full": 0.05,
            "horizon": 21,
        },
    }  # fmt: skip


def test_repo_yaml_equals_defaults(tmp_path: Path) -> None:
    target = tmp_path / "config"
    shutil.copytree(REPO / "config", target)
    assert load_config("dev", config_dir=target).quant.alpha == AlphaConfig()


def test_component_validators() -> None:
    with pytest.raises(ValidationError):
        AlphaComponentConfig(kind="other", source="x", sign=1, weight=0.2)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        AlphaComponentConfig(kind="feature", source="x", sign=2, weight=0.2)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        AlphaComponentConfig(kind="feature", source="", sign=1, weight=0.2)
    with pytest.raises(ValidationError):
        AlphaComponentConfig(kind="feature", source="x", sign=1, weight=-0.1)


def test_multifactor_validators() -> None:
    zero = {"m": AlphaComponentConfig(kind="feature", source="ret_21d", sign=1, weight=0.0)}
    with pytest.raises(ValidationError, match="not all be zero"):
        AlphaMultiFactorConfig(components=zero)
    with pytest.raises(ValidationError):
        AlphaMultiFactorConfig(components={})
    with pytest.raises(ValidationError):
        AlphaMultiFactorConfig(horizon=0)
    with pytest.raises(ValidationError):
        AlphaMultiFactorConfig(min_coverage=1.5)
    with pytest.raises(ValidationError):
        AlphaMultiFactorConfig(weighting="magic")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        AlphaMultiFactorConfig(min_names=2)
    with pytest.raises(ValidationError):
        AlphaMultiFactorConfig(ic_prior=1.5)
    with pytest.raises(ValidationError):
        AlphaMultiFactorConfig(t_full=0.0)


def test_tsmom_validators() -> None:
    with pytest.raises(ValidationError, match="ascending"):
        AlphaTSMOMConfig(lookbacks=(63, 21))
    with pytest.raises(ValidationError):
        AlphaTSMOMConfig(lookbacks=())
    with pytest.raises(ValidationError, match="min_lookbacks"):
        AlphaTSMOMConfig(lookbacks=(21, 63), min_lookbacks=3)
    with pytest.raises(ValidationError):
        AlphaTSMOMConfig(vol_target=0.0)
    with pytest.raises(ValidationError):
        AlphaTSMOMConfig(max_position=0.0)


def test_pead_validators() -> None:
    with pytest.raises(ValidationError):
        AlphaPEADConfig(decay_sessions=0)
    with pytest.raises(ValidationError):
        AlphaPEADConfig(car_full=0.0)
    with pytest.raises(ValidationError):
        AlphaPEADConfig(drift_ann=-0.01)
