"""Stage 16: `factor_panel` end to end on the synthetic world, through `DataView`.

The world's companies grow revenue at a known rate `g` per quarter, so the TTM ratios are
exact: revenue growth = (1+g)^4 - 1, 3-year CAGR = (1+g)^4 - 1 per year, EPS growth =
((1+g)/(1+d))^4 - 1 with share drift `d` per quarter, net issuance = -((1+d)^4 - 1)."""

import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from factor_world import (
    AS_OF,
    SECTORS,
    SPLIT,
    STOCKS,
    FactorWorld,
    build_factor_world,
    world_bars,
)

from atlas.core.config import AtlasConfig, FactorsConfig
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.data.view import DataView
from atlas.quant.factors import (
    FACTOR_NAMES,
    HISTORY_CONCEPTS,
    MARKET_COLUMNS,
    FactorPanel,
    adjusted_shares,
    compute_market,
    factor_panel,
    raw_factors,
)

CFG = FactorsConfig(min_names=5)


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> FactorWorld:
    """One read-only world for the module (tests that write use `fresh_world`)."""
    return build_factor_world(_new_lake(tmp_path_factory.mktemp("factor_world")))


@pytest.fixture(scope="module")
def panel(world: FactorWorld) -> FactorPanel:
    """The world's panel at `AS_OF` with `CFG` (computed once for the module)."""
    return _panel(world.lake)


@pytest.fixture
def fresh_world(tmp_lake: Lake) -> FactorWorld:
    return build_factor_world(tmp_lake)


def _new_lake(root: Path) -> Lake:
    return Lake(root / "lake", registry=DEFAULT_REGISTRY.copy())


def _panel(lake: Lake, day: date = AS_OF, config: FactorsConfig = CFG) -> FactorPanel:
    return factor_panel(DataView(lake, day), config=config)


def test_panel_shape(world: FactorWorld, panel: FactorPanel) -> None:
    p = panel
    assert p.as_of == DataView(world.lake, AS_OF).as_of
    for frame in (p.raw, p.exposures, p.filled):
        assert list(frame.columns) == list(FACTOR_NAMES)
        assert list(frame.index) == list(STOCKS)
    assert list(p.market.columns) == list(MARKET_COLUMNS)
    assert p.sectors.to_dict() == SECTORS
    assert p.raw.notna().all().all(), p.raw.columns[p.raw.isna().any()]
    assert not p.filled.any().any()
    assert len(p.config_hash) == 12


def test_panel_raw_equals_the_pure_pipeline(world: FactorWorld) -> None:
    view = DataView(world.lake, AS_OF)
    p = factor_panel(view, config=CFG)
    panel = view.fundamentals_panel(list(STOCKS))
    history = view.fundamentals_history(list(STOCKS), HISTORY_CONCEPTS)
    splits = view.split_factors(list(STOCKS), 600)
    shares = adjusted_shares(history, splits)
    prices = view.prices(list(STOCKS), CFG.max_price_age_sessions)
    stale = view.config.data.fundamentals.max_staleness_days
    market = compute_market(
        prices,
        shares,
        splits,
        panel,
        day=AS_OF,
        max_staleness_days=stale,
    )
    raw = raw_factors(
        panel,
        history,
        shares,
        market,
        day=AS_OF,
        config=CFG,
        max_staleness_days=stale,
        max_quarter_days=view.config.data.fundamentals.quarter_days[1],
    )
    pd.testing.assert_frame_equal(p.raw, raw)
    pd.testing.assert_frame_equal(p.market, market)


@pytest.mark.parametrize("symbol", STOCKS)
def test_growth_and_issuance_known_values(
    world: FactorWorld, panel: FactorPanel, symbol: str
) -> None:
    c = next(c for c in world.companies if c.symbol == symbol)
    raw = panel.raw.loc[symbol]
    g, d = c.growth, c.share_drift
    assert raw["revenue_growth"] == pytest.approx((1 + g) ** 4 - 1, rel=1e-9, abs=1e-12)
    assert raw["revenue_cagr_3y"] == pytest.approx((1 + g) ** 4 - 1, rel=1e-9, abs=1e-12)
    assert raw["eps_growth"] == pytest.approx(((1 + g) / (1 + d)) ** 4 - 1, rel=1e-9, abs=1e-12)
    assert raw["net_issuance"] == pytest.approx(-((1 + d) ** 4 - 1), rel=1e-9, abs=1e-12)
    k = np.arange(8, 16)  # the last eight of the 16 quarters
    margins = c.gross_margin * (1 + 0.02 * np.sin(k + STOCKS.index(symbol)))
    assert raw["margin_stability"] == pytest.approx(-np.std(margins, ddof=1), rel=1e-9)


def test_value_factors_known_values(world: FactorWorld, panel: FactorPanel) -> None:
    p = panel
    for c in world.companies:
        k = 15
        rev = [c.revenue0 * (1 + c.growth) ** j for j in range(k - 3, k + 1)]
        ttm = sum(rev)
        cap = p.market.loc[c.symbol, "market_cap"]
        r = p.raw.loc[c.symbol]
        assert r["earnings_yield"] == pytest.approx(ttm * c.net_margin / cap, rel=1e-9)
        assert r["sales_ev"] == pytest.approx(ttm / (cap + c.debt - c.cash), rel=1e-9)
        assert r["size"] == pytest.approx(math.log(cap), rel=1e-12)
        assets = c.assets * (1 + c.growth / 4) ** k
        assert r["accruals"] == pytest.approx(
            -(c.net_margin - c.ocf_margin) * ttm / assets, rel=1e-9
        )


def test_split_restates_shares(world: FactorWorld, panel: FactorPanel) -> None:
    sym, _, ratio = SPLIT
    c = next(c for c in world.companies if c.symbol == sym)
    p = panel
    count = c.shares * (1 + c.share_drift) ** 15  # the Q4 2023 cover count, pre-split
    assert p.market.loc[sym, "shares"] == pytest.approx(count * ratio)
    close = world.lake.read_table(
        "prices_daily", filters=[("symbol", "==", sym), ("date", "==", date(2024, 2, 29))]
    )
    assert p.market.loc[sym, "market_cap"] == pytest.approx(
        float(close["close"].iloc[0]) * count * ratio
    )


def test_exposures_invariants(world: FactorWorld) -> None:
    for size_neutral in (False, True):
        p = _panel(world.lake, config=FactorsConfig(min_names=5, size_neutral=size_neutral))
        known = p.sectors.notna().to_numpy()
        e = p.exposures[known]
        assert (e.mean().abs() < 1e-9).all()
        assert ((e.std(ddof=0) - 1).abs() < 1e-9).all()
        assert (e.groupby(p.sectors[known].to_numpy()).sum().abs() < 1e-8).all().all()
        # LLL (no sector) is scored against the market, not zeroed as a group of one
        assert (p.exposures.loc["LLL"].abs() > 1e-6).any()


def test_default_symbols_and_config(world: FactorWorld) -> None:
    cfg = AtlasConfig.model_validate({"quant": {"factors": {"min_names": 5}}})
    view = DataView(world.lake, AS_OF, cfg)
    p = factor_panel(view)
    assert list(p.raw.index) == sorted(STOCKS)
    assert p.exposures.notna().all().all()
    q = factor_panel(DataView(world.lake, AS_OF))  # default min_names 20 > 12 names
    assert q.exposures.isna().all().all() and q.raw.notna().all().all()
    assert q.config_hash != p.config_hash


def test_blocked_symbol_is_absent(fresh_world: FactorWorld) -> None:
    bad = world_bars(["KKK"], date(2024, 2, 29), 600, seed=16 + 1)
    bad.loc[bad["date"] == date(2024, 2, 27), ["open", "high", "low", "close", "adj_close"]] = 0.0
    fresh_world.lake.write_table("prices_daily", bad, mode="upsert")
    p = _panel(fresh_world.lake)
    assert "KKK" not in p.raw.index and len(p.raw) == len(STOCKS) - 1


def test_empty_request(world: FactorWorld) -> None:
    p = factor_panel(DataView(world.lake, AS_OF), symbols=[], config=CFG)
    assert p.raw.empty and list(p.raw.columns) == list(FACTOR_NAMES)
    assert p.exposures.empty and p.market.empty


def test_unknown_instrument_has_unknown_sector(fresh_world: FactorWorld) -> None:
    from factor_world import companies, company_facts, facts_frame

    extra = companies(99)[0]
    extra = type(extra)(**{**extra.__dict__, "symbol": "MMM"})
    fresh_world.lake.write_table(
        "fundamentals_facts", facts_frame(company_facts(extra)), mode="upsert"
    )
    bars = world_bars(["MMM"], date(2024, 2, 29), 600, seed=5)
    fresh_world.lake.write_table("prices_daily", bars, mode="upsert")
    p = factor_panel(DataView(fresh_world.lake, AS_OF), symbols=[*STOCKS, "MMM"], config=CFG)
    assert p.sectors["MMM"] is None
    assert p.raw.loc["MMM"].notna().all()


def test_missing_instruments_table_raises(fresh_world: FactorWorld) -> None:
    # Stage 16 audit: with no instrument row for any name, every name would silently lose its
    # sector (and sector neutralisation with it); that is a data problem, so it raises.
    from atlas.core.errors import DataError

    fresh_world.lake.delete_rows("instruments", [("symbol", "in", list(STOCKS))])
    assert fresh_world.lake.read_table("instruments").empty
    with pytest.raises(DataError, match="instruments"):
        factor_panel(DataView(fresh_world.lake, AS_OF), config=CFG)
