"""Phase 2 checkpoint, Tier 4 item 5 (Stage 15 code): split-adjusted share volume.

`PricePanel.volume` is the raw share volume restated in today's shares (raw volume / the
session's split factor, `DataView.split_factors`); dollar volume needs no restating. The
feature `share_volume_z_21d` uses it, and `compute_features` reads the split factors only
when a requested feature needs them."""

import math
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from factor_world import AS_OF, SPLIT, build_factor_world

from atlas.core.config import AtlasConfig
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.data.view import DataView
from atlas.quant.features import REGISTRY
from atlas.quant.features.registry import PricePanel, compute_features, compute_panel

DAYS = [date(2024, 2, 26), date(2024, 2, 27), date(2024, 2, 28), date(2024, 2, 29)]


def _bars(rows: list[tuple[Any, ...]]) -> pd.DataFrame:
    return pd.DataFrame(
        rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "adj_close"]
    )


BARS = _bars(
    [  # AAA splits 2:1 on 02-28; BBB has no split; CCC has no bars
        ("AAA", DAYS[0], 99.0, 101.0, 98.0, 100.0, 10, 50.0),
        ("AAA", DAYS[1], 101.0, 103.0, 100.0, 102.0, 20, 51.0),
        ("AAA", DAYS[2], 50.5, 52.0, 50.0, 51.0, 40, 51.0),
        ("AAA", DAYS[3], 51.0, 53.0, 50.5, 52.0, 30, 52.0),
        ("BBB", DAYS[0], 9.0, 9.0, 9.0, 9.0, 5, 9.0),
        ("BBB", DAYS[3], 9.0, 9.0, 9.0, 9.0, 7, 9.0),
    ]
)
SPLITS = pd.DataFrame(
    {"AAA": [0.5, 0.5, 1.0, 1.0], "BBB": [1.0] * 4},
    index=pd.DatetimeIndex(pd.to_datetime(DAYS), name="date"),
)


def test_from_bars_restates_share_volume_by_the_split_factor() -> None:
    panel = PricePanel.from_bars(BARS, DAYS, ["AAA", "BBB", "CCC"], splits=SPLITS)
    assert panel.volume is not None
    assert panel.volume["AAA"].tolist() == [20.0, 40.0, 40.0, 30.0]  # pre-split bars x 2
    assert panel.volume["BBB"].tolist()[0] == 5.0 and math.isnan(panel.volume["BBB"].iloc[1])
    assert panel.volume["CCC"].isna().all()  # no bars, no split column: NaN, not 0
    assert panel.dollar_volume["AAA"].tolist() == [1000.0, 2040.0, 2040.0, 1560.0]  # unchanged


def test_without_split_factors_there_is_no_share_volume() -> None:
    assert PricePanel.from_bars(BARS, DAYS, ["AAA"]).volume is None


def test_volume_must_be_aligned() -> None:
    p = PricePanel.from_bars(BARS, DAYS, ["AAA"], splits=SPLITS)
    assert p.volume is not None
    with pytest.raises(ValueError, match="volume"):
        PricePanel(p.open, p.high, p.low, p.close, p.dollar_volume, volume=p.volume.iloc[1:])


def test_share_volume_feature_is_registered_and_needs_volume() -> None:
    spec = REGISTRY.get("share_volume_z_21d")
    assert (spec.lookback, spec.kind, spec.unit, spec.volume) == (22, "ts", "zscore", True)
    assert spec.description
    assert not REGISTRY.get("volume_z_21d").volume


def test_compute_panel_refuses_a_volume_feature_without_volume() -> None:
    panel = PricePanel.from_bars(BARS, DAYS, ["AAA"])
    with pytest.raises(ValueError, match=r"share_volume_z_21d.*share volume"):
        compute_panel(panel, ["share_volume_z_21d"])
    compute_panel(panel, ["volume_z_21d"])  # a dollar-volume feature still works


def test_share_volume_z_known_value() -> None:
    """ln V_t against the mean and std (ddof 1) of ln V over the previous 21 sessions."""
    n = 30
    days = pd.bdate_range("2024-01-01", periods=n)
    volume = np.arange(1, n + 1, dtype="float64") * 100.0
    frame = pd.DataFrame({"AAA": volume}, index=pd.DatetimeIndex(days, name="date"))
    close = pd.DataFrame({"AAA": 10.0}, index=frame.index)
    panel = PricePanel(close, close, close, close, close * frame, volume=frame)
    got = compute_panel(panel, ["share_volume_z_21d"])["share_volume_z_21d"]["AAA"]
    logs = np.log(volume)
    past = logs[-22:-1]
    want = (logs[-1] - past.mean()) / past.std(ddof=1)
    assert got.iloc[-1] == pytest.approx(want, rel=1e-12)
    assert got.iloc[:21].isna().all()


# --- through DataView ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def world_lake(tmp_path_factory: pytest.TempPathFactory) -> Lake:
    root: Path = tmp_path_factory.mktemp("lake")
    lake = Lake(root / "lake", registry=DEFAULT_REGISTRY.copy())
    build_factor_world(lake)
    return lake


def test_compute_features_restates_volume_across_a_known_split(world_lake: Lake) -> None:
    """CCC splits 2:1 on 2024-02-12 (known at the as-of): the feature equals the formula on
    raw volume / `DataView.split_factors`, so the bars before the split count double."""
    view = DataView(world_lake, AS_OF, AtlasConfig())
    symbol, ex_date, ratio = SPLIT
    got = compute_features(view, [symbol], ["share_volume_z_21d"], history=30)
    n = REGISTRY.get("share_volume_z_21d").lookback + view.config.quant.features.buffer_sessions
    n += 30 - 1
    bars = view.prices([symbol], n).set_index("date")["volume"].astype("float64")
    factors = view.split_factors([symbol], n)[symbol]
    shares = bars.to_numpy() / factors.to_numpy()
    assert factors.iloc[0] == 1 / ratio and factors.iloc[-1] == 1.0
    logs = np.log(shares)
    want = []
    for t in range(n - 30, n):
        past = logs[t - 21 : t]
        sd = past.std(ddof=1)
        want.append((logs[t] - past.mean()) / sd if sd > 1e-12 else math.nan)
    values = got["share_volume_z_21d"][symbol].to_numpy()
    assert values == pytest.approx(want, rel=1e-9, nan_ok=True)
    # The world trades a constant raw volume, so only a session after the split, whose window
    # (the 21 sessions before it) still holds pre-split bars, has a value: in today's shares
    # those bars count double. On the ex-date itself the window is all pre-split: no value.
    index = got["share_volume_z_21d"].index
    assert pd.Timestamp(ex_date) in index
    from_split = index > pd.Timestamp(ex_date)
    assert np.isfinite(values).tolist() == from_split.tolist()
