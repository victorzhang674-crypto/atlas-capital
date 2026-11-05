"""Stage 21 Task 6: the daily regime history persisted in the lake (`regime_history`)."""

from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig, QuantConfig, RegimeConfig, RegimeScalarConfig
from atlas.core.pit import AsOf, bar_available_at
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.quant.regime.state import RegimeState
from atlas.quant.regime.store import (
    REGIME_HISTORY,
    RegimeStore,
    build_and_store,
    regime_config_hash,
)

CAL = TradingCalendar()


def _state(day: date, **over: object) -> RegimeState:
    base: dict[str, object] = {
        "as_of": day,
        "data_date": CAL.offset(day, -1),
        "vol_regime": "HIGH",
        "trend_regime": "NEUTRAL",
        "hmm_probs": (0.2, 0.3, 0.5),
        "hmm_labels": ("calm", "normal", "turbulent"),
        "macro_quadrant": "Reflation",
        "stress_flag": False,
        "risk_scalar": 0.5355,
        "credit_source": "proxy",
        "realised_vol": 0.21,
        "vix": 24.5,
        "vol_score": 0.8,
        "price_to_sma_200": 0.98,
        "momentum_12_1": 0.04,
        "breadth": 0.47,
        "credit_z": 1.2,
        "nfci": -0.3,
        "growth_score": 1 / 3,
        "inflation_gap": 0.4,
        "missing": (),
    }
    base.update(over)
    return RegimeState(**base)  # type: ignore[arg-type]


@pytest.fixture
def store(tmp_path: Path) -> RegimeStore:
    return RegimeStore(Lake(tmp_path / "out", registry=DEFAULT_REGISTRY.copy()))


def test_table_schema() -> None:
    assert REGIME_HISTORY.name == "regime_history"
    assert REGIME_HISTORY.primary_key == ("date",)
    assert "regime_history" in DEFAULT_REGISTRY.names()


def test_round_trip(store: RegimeStore) -> None:
    states = [
        _state(date(2020, 3, 16)),
        _state(
            date(2020, 3, 17),
            hmm_probs=None,
            hmm_labels=(),
            macro_quadrant=None,
            vix=None,
            credit_source=None,
            credit_z=None,
            missing=("vix", "hmm", "macro_quadrant", "credit"),
        ),
        _state(
            date(2020, 3, 18),
            hmm_probs=(0.9, 0.1),
            hmm_labels=("calm", "turbulent"),
            stress_flag=True,
        ),
    ]
    assert store.write(states) == 3
    assert store.read(date(2020, 3, 1), date(2020, 3, 31)) == states
    assert store.dates() == (date(2020, 3, 16), date(2020, 3, 17), date(2020, 3, 18))


def test_point_in_time_read(store: RegimeStore) -> None:
    day = date(2020, 3, 17)
    store.write([_state(date(2020, 3, 16)), _state(day)])
    rows = store._lake.read_table("regime_history")
    assert set(rows["available_at"]) == {
        pd.Timestamp(bar_available_at(d, CAL)) for d in (date(2020, 3, 13), date(2020, 3, 16))
    }
    assert store.latest(day).as_of == day  # the decision of day sees its own state
    assert store.latest(
        AsOf(day=date(2020, 3, 16), cutoff=datetime(2020, 3, 16, 20, 59, tzinfo=UTC))
    ).as_of == date(2020, 3, 16)
    assert store.latest(date(2020, 3, 13)) is None  # before any stored state is known
    assert store.read(date(2020, 3, 1), date(2020, 3, 31), as_of=date(2020, 3, 16)) == [
        _state(date(2020, 3, 16))
    ]


def test_rewrite_replaces_the_same_dates(store: RegimeStore) -> None:
    day = date(2020, 3, 16)
    store.write([_state(day), _state(date(2020, 3, 17))])
    store.write([_state(day, vol_regime="CRISIS", risk_scalar=0.25)])
    got = store.read(date(2020, 3, 1), date(2020, 3, 31))
    assert [s.vol_regime for s in got] == ["CRISIS", "HIGH"]


def test_other_settings_are_ignored(tmp_path: Path) -> None:
    lake = Lake(tmp_path / "out", registry=DEFAULT_REGISTRY.copy())
    other = AtlasConfig(
        quant=QuantConfig(regime=RegimeConfig(risk_scalar=RegimeScalarConfig(stress=0.5)))
    )
    assert regime_config_hash(other) != regime_config_hash(AtlasConfig())
    RegimeStore(lake, other).write([_state(date(2020, 3, 16))])
    assert RegimeStore(lake).read(date(2020, 3, 1), date(2020, 3, 31)) == []
    assert RegimeStore(lake).latest(date(2020, 3, 31)) is None


def test_empty_store(store: RegimeStore) -> None:
    assert store.read(date(2020, 1, 1), date(2020, 12, 31)) == []
    assert store.dates() == ()
    assert store.latest(date(2020, 3, 2)) is None
    assert store.write([]) == 0


def test_bad_arguments(store: RegimeStore) -> None:
    with pytest.raises(ValueError):
        store.read(date(2020, 3, 2), date(2020, 3, 1))
    with pytest.raises(TypeError):
        store.latest(datetime(2020, 3, 2, tzinfo=UTC))  # type: ignore[arg-type]


def test_build_and_store(tmp_path: Path) -> None:
    from regime_world import build_regime_world

    world = build_regime_world(Lake(tmp_path / "data", registry=DEFAULT_REGISTRY.copy()))
    out = RegimeStore(Lake(tmp_path / "out", registry=DEFAULT_REGISTRY.copy()), world.config)
    states = build_and_store(world.lake, out, date(2020, 3, 2), date(2020, 3, 13), world.config)
    assert [s.as_of for s in states] == list(CAL.sessions(date(2020, 3, 2), date(2020, 3, 13)))
    assert out.read(date(2020, 3, 2), date(2020, 3, 13)) == states
