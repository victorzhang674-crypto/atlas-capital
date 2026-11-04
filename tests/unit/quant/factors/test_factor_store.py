"""Stage 16 Task 4 of the brief: the factor panel cache in the lake
(`factor_exposures(date, symbol, factor, value, ...)`) for monthly rebalance dates."""

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from factor_world import STOCKS, build_factor_world

from atlas.core.config import AtlasConfig
from atlas.core.errors import LookAheadError
from atlas.core.pit import AsOf
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.data.view import DataView
from atlas.quant.factors import (
    FACTOR_EXPOSURES,
    FACTOR_NAMES,
    MARKET_COLUMNS,
    FactorPanel,
    FactorStore,
    build_factor_cache,
    factor_config_hash,
    factor_panel,
    monthly_rebalance_dates,
)

CFG = AtlasConfig.model_validate({"quant": {"factors": {"min_names": 5}}})
TAG = factor_config_hash(CFG)


def _panel(
    day: date, seed: int = 0, tag: str = TAG, symbols: tuple[str, ...] = STOCKS
) -> FactorPanel:
    rng = np.random.default_rng(seed)
    index = pd.Index(list(symbols), name="symbol")
    raw = pd.DataFrame(
        rng.normal(size=(len(index), len(FACTOR_NAMES))), index=index, columns=list(FACTOR_NAMES)
    )
    raw.iloc[0, 3] = np.nan
    exposures = raw.fillna(0.0) * 0.5
    exposures["eps_growth"] = np.nan  # a factor left all NaN (too few names)
    filled = raw.isna()
    market = pd.DataFrame(index=index, columns=list(MARKET_COLUMNS))
    return FactorPanel(
        as_of=AsOf.for_session(day),
        raw=raw,
        exposures=exposures,
        filled=filled,
        market=market,
        sectors=pd.Series([None] * len(index), index=index, dtype="object"),
        config_hash=tag,
    )


def test_schema() -> None:
    assert FACTOR_EXPOSURES.name == "factor_exposures"
    assert FACTOR_EXPOSURES.column_names[:4] == ("date", "symbol", "factor", "value")
    assert FACTOR_EXPOSURES.primary_key == ("date", "symbol", "factor")
    assert "factor_exposures" in DEFAULT_REGISTRY


def test_round_trip(tmp_lake: Lake) -> None:
    store = FactorStore(tmp_lake, CFG)
    panel = _panel(date(2024, 1, 31))
    assert store.write(panel) == len(STOCKS) * len(FACTOR_NAMES)
    got = store.read(date(2024, 1, 31))
    assert got is not None
    assert got.date == date(2024, 1, 31) and got.config_hash == TAG
    pd.testing.assert_frame_equal(got.exposures, panel.exposures, check_names=False)
    pd.testing.assert_frame_equal(got.raw, panel.raw, check_names=False)
    pd.testing.assert_frame_equal(got.filled, panel.filled, check_names=False)
    rows = tmp_lake.read_table("factor_exposures")
    cutoff = pd.Timestamp(panel.as_of.cutoff)
    assert (rows["available_at"] == cutoff - pd.Timedelta(microseconds=1)).all()


def test_read_latest_date_not_after_the_as_of(tmp_lake: Lake) -> None:
    store = FactorStore(tmp_lake, CFG)
    store.write(_panel(date(2024, 1, 31), seed=1))
    store.write(_panel(date(2024, 2, 29), seed=2))
    assert store.dates() == (date(2024, 1, 31), date(2024, 2, 29))
    assert store.read(date(2024, 2, 28)).date == date(2024, 1, 31)  # type: ignore[union-attr]
    assert store.read(date(2024, 3, 15)).date == date(2024, 2, 29)  # type: ignore[union-attr]
    assert store.read(date(2024, 1, 30)) is None
    # the same day before its cutoff: the rows computed at the cutoff are not yet known
    early = AsOf(
        day=date(2024, 2, 29),
        cutoff=AsOf.for_session(date(2024, 2, 29)).cutoff - timedelta(hours=1),
    )
    assert store.read(early).date == date(2024, 1, 31)  # type: ignore[union-attr]


def test_rewrite_replaces_the_day(tmp_lake: Lake) -> None:
    store = FactorStore(tmp_lake, CFG)
    store.write(_panel(date(2024, 1, 31), seed=1))
    smaller = _panel(date(2024, 1, 31), seed=3, symbols=STOCKS[:4])
    store.write(smaller)
    got = store.read(date(2024, 1, 31))
    assert got is not None
    assert list(got.exposures.index) == list(STOCKS[:4])
    pd.testing.assert_frame_equal(got.raw, smaller.raw, check_names=False)


def test_other_config_hash_not_used(tmp_lake: Lake) -> None:
    other = AtlasConfig.model_validate({"quant": {"factors": {"min_names": 6}}})
    assert factor_config_hash(other) != TAG
    FactorStore(tmp_lake, other).write(_panel(date(2024, 1, 31), tag=factor_config_hash(other)))
    store = FactorStore(tmp_lake, CFG)
    assert store.read(date(2024, 2, 1)) is None
    assert store.dates() == ()


def test_write_rejects_a_foreign_hash(tmp_lake: Lake) -> None:
    with pytest.raises(ValueError, match="config_hash"):
        FactorStore(tmp_lake, CFG).write(_panel(date(2024, 1, 31), tag="x" * 12))


def test_read_without_table(tmp_lake: Lake) -> None:
    store = FactorStore(tmp_lake, CFG)
    assert store.read(date(2024, 1, 31)) is None
    assert store.dates() == ()


def test_guard_raises_if_a_later_row_slips_through(
    tmp_lake: Lake, monkeypatch: pytest.MonkeyPatch
) -> None:
    import atlas.quant.factors.store as store_module

    store = FactorStore(tmp_lake, CFG)
    store.write(_panel(date(2024, 1, 31)))
    monkeypatch.setattr(store_module, "pit_filter", lambda frame, as_of: frame)
    early = AsOf(
        day=date(2024, 1, 31),
        cutoff=AsOf.for_session(date(2024, 1, 31)).cutoff - timedelta(hours=1),
    )
    with pytest.raises(LookAheadError):
        store.read(early)


def test_monthly_rebalance_dates() -> None:
    days = monthly_rebalance_dates(date(2023, 11, 15), date(2024, 3, 27))
    assert days == (date(2023, 11, 30), date(2023, 12, 29), date(2024, 1, 31), date(2024, 2, 29))
    assert monthly_rebalance_dates(date(2024, 3, 1), date(2024, 3, 27)) == ()
    # 2024-03-29 is Good Friday (market closed): March's last session is the 28th
    assert monthly_rebalance_dates(date(2024, 3, 1), date(2024, 3, 28)) == (date(2024, 3, 28),)
    with pytest.raises(ValueError, match="start"):
        monthly_rebalance_dates(date(2024, 3, 1), date(2024, 1, 1))


def test_build_factor_cache(tmp_path: Path) -> None:
    lake = Lake(tmp_path / "lake", registry=DEFAULT_REGISTRY.copy())
    build_factor_world(lake)
    days = (date(2024, 1, 31), date(2024, 2, 29))
    assert build_factor_cache(lake, days, config=CFG) == days
    store = FactorStore(lake, CFG)
    assert store.dates() == days
    got = store.read(date(2024, 2, 29))
    want = factor_panel(DataView(lake, date(2024, 2, 29), CFG))
    assert got is not None
    pd.testing.assert_frame_equal(got.exposures, want.exposures, check_names=False)
    pd.testing.assert_frame_equal(got.raw, want.raw, check_names=False)
    # stored dates are skipped unless refresh=True
    assert build_factor_cache(lake, days, config=CFG) == ()
    assert build_factor_cache(lake, days[1:], config=CFG, refresh=True) == days[1:]
