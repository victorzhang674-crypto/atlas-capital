"""Stage 22 Task 4: the post-earnings-drift signal and its decay."""

from datetime import UTC, date, datetime

import pandas as pd
import pytest

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig
from atlas.core.errors import ConfigError
from atlas.core.pit import bar_available_at
from atlas.core.storage import Lake
from atlas.data.events import REACTION_COLUMNS
from atlas.data.view import DataView
from atlas.quant.alpha import AlphaModel, PEADModel, check_alpha_frame, pead_decay, pead_signal
from tiny_universe import TinyUniverse, build_tiny_universe

CAL = TradingCalendar()
W1 = date(2023, 11, 1)  # AAA's reaction window ends here
W0 = date(2023, 8, 1)  # BBB's older reaction
W2 = date(2023, 11, 15)  # BBB's newer reaction


def _reaction(symbol: str, event_id: str, window_end: date, car: float) -> dict[str, object]:
    d0 = CAL.offset(window_end, -2)
    return {
        "symbol": symbol,
        "event_id": event_id,
        "event_time": datetime.combine(CAL.prev_session(d0), datetime.min.time(), UTC),
        "reaction_date": d0,
        "window_end": window_end,
        "gap_return": car / 2,
        "oc_return": car / 2,
        "oc_abnormal": car / 2,
        "car": car,
        "benchmark": "SPY",
        "available_at": bar_available_at(window_end, CAL),
    }


@pytest.fixture(scope="module")
def tu(tmp_path_factory: pytest.TempPathFactory) -> TinyUniverse:
    world = build_tiny_universe(Lake(tmp_path_factory.mktemp("pead")))
    rows = [
        _reaction("AAA", "a1", W1, 0.04),
        _reaction("BBB", "b0", W0, 0.06),
        _reaction("BBB", "b2", W2, -0.02),
        _reaction("CCC", "c1", W1, 0.0),
    ]
    frame = pd.DataFrame(rows, columns=list(REACTION_COLUMNS))
    for col in ("event_time", "available_at"):
        frame[col] = pd.to_datetime(frame[col], utc=True).dt.as_unit("us")
    world.lake.write_table("earnings_reactions", frame, mode="upsert")
    return world


def _predict(tu: TinyUniverse, day: date) -> pd.DataFrame:
    return PEADModel().predict(DataView(tu.lake, day))


def test_decay_known_values() -> None:
    assert pead_decay(0, 60) == 1.0
    assert pead_decay(30, 60) == 0.5
    assert pead_decay(59, 60) == pytest.approx(1 / 60)
    assert pead_decay(60, 60) == 0.0
    assert pead_decay(200, 60) == 0.0
    with pytest.raises(ValueError):
        pead_decay(-1, 60)


def test_pead_signal_pure() -> None:
    latest = pd.DataFrame({"car": [0.04, -0.10, 0.0], "age": [0, 30, 10]}, index=["A", "B", "C"])
    out = pead_signal(latest, decay_sessions=60, drift_ann=0.06, car_full=0.05)
    assert out.loc["A", "score"] == 1.0
    assert out.loc["A", "exp_return_ann"] == pytest.approx(0.06)
    assert out.loc["A", "confidence"] == pytest.approx(0.8)  # 0.04 / 0.05
    assert out.loc["B", "score"] == -0.5
    assert out.loc["B", "confidence"] == pytest.approx(0.5)  # strength capped at 1
    assert out.loc["C", "score"] == 0.0 and out.loc["C", "confidence"] == 0.0
    check_alpha_frame(out)


def test_decays_linearly_with_the_decision_day(tu: TinyUniverse) -> None:
    model = PEADModel()
    assert isinstance(model, AlphaModel) and model.name == "pead" and model.horizon == 21
    first = CAL.next_session(W1)  # first decision that knows AAA's reaction: k = 0
    assert _predict(tu, first).loc["AAA", "score"] == 1.0
    half = CAL.offset(W1, 31)  # k = 30 sessions after the window
    out = _predict(tu, half)
    assert out.loc["AAA", "score"] == pytest.approx(0.5)
    assert out.loc["AAA", "exp_return_ann"] == pytest.approx(0.03)
    check_alpha_frame(out)
    scores = [_predict(tu, CAL.offset(W1, 1 + k)).loc["AAA", "score"] for k in (0, 15, 45)]
    assert scores == pytest.approx([1.0, 0.75, 0.25])


def test_not_known_before_the_window_closes(tu: TinyUniverse) -> None:
    assert "AAA" not in _predict(tu, W1).index  # 08:00 on the window's last day: not known


def test_drops_out_after_decay_sessions(tu: TinyUniverse) -> None:
    assert "AAA" in _predict(tu, CAL.offset(W1, 60)).index  # k = 59
    assert "AAA" not in _predict(tu, CAL.offset(W1, 61)).index  # k = 60


def test_newer_reaction_replaces_older(tu: TinyUniverse) -> None:
    before = _predict(tu, CAL.next_session(W1))
    assert "BBB" not in before.index  # b0 is older than 60 sessions by then
    out = _predict(tu, CAL.next_session(W2))
    assert out.loc["BBB", "score"] == -1.0  # b2 (car -0.02), fresh
    assert out.loc["BBB", "confidence"] == pytest.approx(0.4)


def test_zero_car_and_names_without_reactions(tu: TinyUniverse) -> None:
    out = _predict(tu, CAL.next_session(W1))
    assert out.loc["CCC", "score"] == 0.0
    assert out.loc["CCC", "exp_return_ann"] == 0.0
    assert "DDD" not in out.index  # no reaction at all


def test_horizon_must_be_the_one_the_view_configures(tu: TinyUniverse) -> None:  # Tier 2
    cfg = AtlasConfig.model_validate({"quant": {"alpha": {"pead": {"horizon": 42}}}})
    view = DataView(tu.lake, CAL.next_session(W1), cfg)
    with pytest.raises(ConfigError, match=r"horizon.*21.*42"):
        PEADModel().predict(view)
    model = PEADModel(config=cfg.quant.alpha.pead)
    assert model.horizon == 42
    check_alpha_frame(model.predict(view))
