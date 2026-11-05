"""Stage 21 Task 4: the `risk_scalar` mapping table and the `RegimeState` contract."""

import itertools
import math
from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from atlas.core.config import RegimeConfig, RegimeScalarConfig
from atlas.quant.regime.macro_quadrant import QUADRANTS
from atlas.quant.regime.state import RegimeState, risk_scalar
from atlas.quant.regime.vol_trend import TREND_LABELS, VOL_LABELS

CFG = RegimeConfig()
VOL_M = {"LOW": 1.0, "NORMAL": 1.0, "HIGH": 0.7, "CRISIS": 0.4}
TREND_M = {"UP": 1.0, "NEUTRAL": 0.9, "DOWN": 0.75}
QUAD_M = {"Goldilocks": 1.0, "Reflation": 1.0, "Stagflation": 0.85, "Deflation": 0.9, None: 1.0}


@pytest.mark.parametrize(
    ("vol", "trend", "stress", "p_turb", "quad"),
    list(
        itertools.product(
            VOL_LABELS, TREND_LABELS, (False, True), (None, 0.2, 0.5, 0.9), (*QUADRANTS, None)
        )
    ),
)
def test_risk_scalar_full_table(vol, trend, stress, p_turb, quad) -> None:  # type: ignore[no-untyped-def]
    expected = VOL_M[vol] * TREND_M[trend] * (0.6 if stress else 1.0)
    expected *= 0.85 if (p_turb is not None and p_turb >= 0.5) else 1.0
    expected *= QUAD_M[quad]
    expected = min(1.0, max(0.25, expected))
    got = risk_scalar(vol, trend, stress=stress, quadrant=quad, p_turbulent=p_turb, config=CFG)
    assert got == pytest.approx(expected, abs=1e-12)
    assert 0.25 <= got <= 1.0


def test_risk_scalar_examples() -> None:
    calm = risk_scalar(
        "LOW", "UP", stress=False, quadrant="Goldilocks", p_turbulent=0.01, config=CFG
    )
    assert calm == 1.0
    worst = risk_scalar(
        "CRISIS", "DOWN", stress=True, quadrant="Stagflation", p_turbulent=0.99, config=CFG
    )
    assert worst == 0.25  # 0.4 * 0.75 * 0.6 * 0.85 * 0.85 = 0.13 -> floor


def test_risk_scalar_uses_the_config_table() -> None:
    cfg = RegimeConfig(risk_scalar=RegimeScalarConfig(floor=0.5, stress=0.9))
    assert (
        risk_scalar("CRISIS", "DOWN", stress=True, quadrant=None, p_turbulent=None, config=cfg)
        == 0.5
    )
    assert risk_scalar(
        "LOW", "UP", stress=True, quadrant=None, p_turbulent=None, config=cfg
    ) == pytest.approx(0.9)


@given(
    st.sampled_from(VOL_LABELS), st.sampled_from(VOL_LABELS),
    st.sampled_from(TREND_LABELS), st.sampled_from(TREND_LABELS),
    st.booleans(), st.sampled_from([*QUADRANTS, None]),
    st.one_of(st.none(), st.floats(0, 1)),
)  # fmt: skip
def test_worse_labels_never_raise_the_scalar(v1, v2, t1, t2, stress, quad, p) -> None:  # type: ignore[no-untyped-def]
    order_v, order_t = VOL_LABELS.index, TREND_LABELS.index
    if order_v(v1) > order_v(v2):
        v1, v2 = v2, v1
    if order_t(t1) > order_t(t2):
        t1, t2 = t2, t1
    better = risk_scalar(v1, t1, stress=False, quadrant=quad, p_turbulent=p, config=CFG)
    worse = risk_scalar(v2, t2, stress=stress, quadrant=quad, p_turbulent=p, config=CFG)
    assert worse <= better


def test_risk_scalar_rejects_unknown_labels() -> None:
    with pytest.raises(ValueError):
        risk_scalar("EXTREME", "UP", stress=False, quadrant=None, p_turbulent=None, config=CFG)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        risk_scalar("LOW", "UP", stress=False, quadrant=None, p_turbulent=1.5, config=CFG)


# --- RegimeState ------------------------------------------------------------------------------


def _state(**over: object) -> RegimeState:
    base: dict[str, object] = {
        "as_of": date(2020, 3, 16),
        "data_date": date(2020, 3, 13),
        "vol_regime": "CRISIS",
        "trend_regime": "DOWN",
        "hmm_probs": (0.05, 0.15, 0.80),
        "hmm_labels": ("calm", "normal", "turbulent"),
        "macro_quadrant": "Deflation",
        "stress_flag": True,
        "risk_scalar": 0.25,
        "credit_source": "proxy",
    }
    base.update(over)
    return RegimeState(**base)  # type: ignore[arg-type]


def test_state_fields_and_turbulent_probability() -> None:
    s = _state()
    assert s.p_turbulent == pytest.approx(0.80)
    assert _state(hmm_probs=None, hmm_labels=()).p_turbulent is None
    assert s.missing == () and s.realised_vol is None


def test_state_is_frozen_and_strict() -> None:
    s = _state()
    with pytest.raises(ValidationError):
        s.risk_scalar = 0.5  # type: ignore[misc]
    with pytest.raises(ValidationError):
        _state(unknown=1)


@pytest.mark.parametrize(
    "over",
    [
        {"vol_regime": "EXTREME"},
        {"trend_regime": "SIDEWAYS"},
        {"macro_quadrant": "Boom"},
        {"risk_scalar": 0.2},
        {"risk_scalar": 1.01},
        {"risk_scalar": math.nan},
        {"data_date": date(2020, 3, 16)},  # must be before the decision day
        {"hmm_probs": (0.5, 0.6, 0.1)},  # does not sum to 1
        {"hmm_probs": (0.5, 0.5)},  # length differs from the labels
        {"hmm_probs": (1.2, -0.1, -0.1)},
        {"hmm_probs": None},  # labels without probabilities
        {"hmm_labels": ("calm", "normal", "wild")},
        {"credit_source": "bloomberg"},
    ],
)
def test_state_validation(over: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _state(**over)


def test_state_json_round_trip() -> None:
    s = _state(missing=("nfci",), realised_vol=0.62, vix=82.7)
    assert RegimeState.model_validate_json(s.model_dump_json()) == s
