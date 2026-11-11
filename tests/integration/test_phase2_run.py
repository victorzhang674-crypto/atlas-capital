"""Phase 2 end to end, offline (phase 2 checkpoint): one synthetic lake carried through the
whole quant engine, so CI covers the chain the real-lake integration tests cover only where a
lake exists.

features (15) -> factor panels and their lake cache via `atlas factors build` (16) -> daily
factor returns and month-end model snapshots via `atlas risk-model build` (19, on the
covariance tools of 18) -> alpha frame (22) -> a long/short book's risk from the stored model
(19) and from the metrics library (20) -> a signal tear sheet (17) scored by the library.

The world is 36 companies in three sectors with three years of quarterly filings, plus two
ETFs (SPY, the benchmark, and TLT), 600 sessions of random-walk bars to 2024-02-29. Prices
carry no planted effect: the test checks that the pieces fit and their invariants hold, not
that any signal works.
"""

import re
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml
from typer.testing import CliRunner

_FACTORS = str(Path(__file__).resolve().parents[1] / "unit" / "quant" / "factors")
if _FACTORS not in sys.path:
    sys.path.append(_FACTORS)

import factor_world as fw  # noqa: E402

from atlas.cli.app import app  # noqa: E402
from atlas.core.calendar import TradingCalendar  # noqa: E402
from atlas.core.config import AtlasConfig, load_config  # noqa: E402
from atlas.core.storage import DEFAULT_REGISTRY, Lake  # noqa: E402
from atlas.core.types.instruments import AssetClass, Instrument  # noqa: E402
from atlas.data.reference import instruments_frame  # noqa: E402
from atlas.data.view import DataView  # noqa: E402
from atlas.quant.alpha import MultiFactorModel, check_alpha_frame  # noqa: E402
from atlas.quant.factors import FACTOR_NAMES, FactorStore, factor_panel  # noqa: E402
from atlas.quant.features import compute_features  # noqa: E402
from atlas.quant.metrics import (  # noqa: E402
    performance_summary,
    portfolio_var,
    risk_contributions,
    sharpe_ratio,
)
from atlas.quant.research import research_signal  # noqa: E402
from atlas.quant.risk_model.analytics import portfolio_risk  # noqa: E402
from atlas.quant.risk_model.psd import is_psd  # noqa: E402
from atlas.quant.risk_model.store import RiskModelStore  # noqa: E402

pytestmark = pytest.mark.integration

CAL = TradingCalendar()
LAST = date(2024, 2, 29)  # the last bar in the lake
AS_OF = date(2024, 3, 1)  # the decision morning that knows it
SECTORS = ("Information Technology", "Financials", "Health Care")
STOCKS = tuple(f"S{i:02d}" for i in range(36))
ETFS = ("SPY", "TLT")
MONTH_ENDS = (
    date(2023, 9, 29),
    date(2023, 10, 31),
    date(2023, 11, 30),
    date(2023, 12, 29),
    date(2024, 1, 31),
    LAST,
)
SETTINGS = {
    "fund": {"benchmark": "SPY"},
    "quant": {
        "factors": {"min_names": 5},
        "risk_model": {
            "factor_model": {
                "sectors": list(SECTORS),
                "min_cross_section": 20,
                "etf": {"window": 60, "min_obs": 40},
            }
        },
    },
}
UNIVERSES = {
    "sp500": {"description": "test members", "index": "SP500", "tickers": []},
    "macro_etfs": {"description": "test ETFs", "index": None, "tickers": list(ETFS)},
}


def _companies(seed: int = 23) -> list[fw.Company]:
    rng = np.random.default_rng(seed)
    return [
        fw.Company(
            symbol=sym,
            revenue0=float(rng.uniform(200e6, 2e9)),
            growth=float(rng.uniform(-0.02, 0.05)),
            gross_margin=float(rng.uniform(0.2, 0.7)),
            op_margin=float(rng.uniform(0.05, 0.3)),
            net_margin=float(rng.uniform(0.02, 0.2)),
            ocf_margin=float(rng.uniform(0.05, 0.3)),
            capex_ratio=float(rng.uniform(0.02, 0.1)),
            assets=float(rng.uniform(1e9, 1e10)),
            equity=float(rng.uniform(3e8, 4e9)),
            debt=float(rng.uniform(0, 3e9)),
            cash=float(rng.uniform(5e7, 1e9)),
            shares=float(rng.uniform(5e7, 5e8)),
            share_drift=float(rng.uniform(-0.01, 0.01)),
            eps_scale=1.0,
        )
        for sym in STOCKS
    ]


def _build_world(lake: Lake) -> None:
    lake.write_table("prices_daily", fw.world_bars([*STOCKS, *ETFS], LAST, 600), mode="upsert")
    instruments = [
        Instrument(symbol=s, asset_class=AssetClass.EQUITY, name=f"{s} Inc", sector=SECTORS[i % 3])
        for i, s in enumerate(STOCKS)
    ] + [Instrument(symbol=s, asset_class=AssetClass.ETF, name=f"{s} ETF") for s in ETFS]
    lake.write_table("instruments", instruments_frame(instruments), mode="upsert")
    membership = pd.DataFrame(
        {
            "index_id": "SP500",
            "symbol": list(STOCKS),
            "start_date": date(2010, 1, 4),
            "end_date": None,
        }
    )
    lake.write_table("index_membership", membership, mode="upsert")
    facts = [row for company in _companies() for row in fw.company_facts(company)]
    lake.write_table("fundamentals_facts", fw.facts_frame(facts), mode="upsert")


class World:
    """The lake after both build commands ran, with their outputs and the config they used."""

    def __init__(self, root: Path) -> None:
        self.lake = Lake(root / "lake", registry=DEFAULT_REGISTRY.copy())
        _build_world(self.lake)
        self.config_dir = root / "config"
        self.config_dir.mkdir()
        (self.config_dir / "fund.yaml").write_text(yaml.safe_dump(SETTINGS))
        (self.config_dir / "universe.yaml").write_text(yaml.safe_dump(UNIVERSES))
        self.env = {
            "COLUMNS": "200",
            "NO_COLOR": "1",
            "ATLAS__DATA__LAKE_PATH": str(self.lake.root),
        }
        self.factors_out = self.run("factors", "build", "--from", "2023-09-01", "--to", "2024")
        self.risk_out = self.run("risk-model", "build", "--from", "2023-09-01", "--to", "2024")
        self.config: AtlasConfig = load_config(config_dir=self.config_dir)

    def run(self, *args: str) -> str:
        result = CliRunner().invoke(
            app, ["--config-dir", str(self.config_dir), *args], env=self.env
        )
        assert result.exit_code == 0, result.output
        return re.sub(r"\x1b\[[0-9;]*m", "", result.output)

    def view(self, day: date = AS_OF) -> DataView:
        return DataView(Lake(self.lake.root), day, self.config)


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    return World(tmp_path_factory.mktemp("phase2"))


def test_features_feed_every_member(world: World) -> None:
    names = ["momentum_12_1", "vol_63d", "beta_252d", "amihud_21d"]
    features = compute_features(world.view(), list(STOCKS), names)
    for name in names:
        row = features[name].iloc[-1]
        assert list(row.index) == list(STOCKS) and row.notna().all(), name
        assert features[name].index[-1] == pd.Timestamp(LAST)  # nothing from the decision day


def test_factors_build_filled_the_cache_point_in_time(world: World) -> None:
    assert "6 month-ends" in world.factors_out and "6 written" in world.factors_out
    store = FactorStore(Lake(world.lake.root), world.config)
    assert store.dates() == MONTH_ENDS
    stored = store.read(LAST)
    fresh = factor_panel(world.view(LAST))
    assert stored is not None and stored.date == LAST
    assert list(stored.exposures.columns) == list(FACTOR_NAMES)
    pd.testing.assert_frame_equal(stored.exposures, fresh.exposures, check_names=False)
    assert len(stored.exposures) == len(STOCKS)
    # the cleaning invariants of Stage 16 on what later stages will read
    values = stored.exposures.to_numpy(dtype="float64")
    assert np.isfinite(values).all()
    assert np.abs(values.mean(axis=0)).max() < 1e-9
    # a panel is known from its own decision morning on, not before
    before = store.read(CAL.offset(MONTH_ENDS[0], -1))
    assert before is None


def test_risk_model_build_stored_returns_and_snapshots(world: World) -> None:
    out = world.risk_out
    assert "124 sessions fitted from 2023-09-01 to 2024-02-29" in out and "0 skipped" in out
    assert "4 snapshots from 2023-11-30 to 2024-02-29 with 2 ETFs" in out
    store = RiskModelStore(Lake(world.lake.root), world.config)
    assert store.snapshot_dates() == MONTH_ENDS[2:]
    returns = store.read_factor_returns(AS_OF)
    assert returns is not None and len(returns) == 124
    assert returns.index[-1] == pd.Timestamp(LAST)
    present = returns.dropna(axis=1, how="all").columns
    assert {"market", *SECTORS, "momentum", "value", "size", "beta"} <= set(present)


def test_stored_model_is_point_in_time_and_valid(world: World) -> None:
    store = RiskModelStore(Lake(world.lake.root), world.config)
    model = store.read_snapshot(AS_OF)
    assert model is not None and model.as_of == LAST
    # on the morning of the month-end itself only the previous month's model is known
    earlier = store.read_snapshot(LAST)
    assert earlier is not None and earlier.as_of == MONTH_ENDS[-2]
    assert store.read_snapshot(MONTH_ENDS[2]) is None  # nothing stored before 2023-11-30
    assert set(model.etfs) == set(ETFS)
    assert set(STOCKS) <= set(model.symbols) and not model.dropped
    cov = model.cov()
    assert is_psd(cov.to_numpy()) and np.allclose(cov, cov.T)
    vols = model.vol()
    assert ((vols > 0.002) & (vols < 0.05)).all()  # daily vols of 1%-a-day random walks


@pytest.fixture(scope="module")
def alpha(world: World) -> pd.DataFrame:
    return MultiFactorModel().predict(world.view())


def test_alpha_frame_meets_the_contract(world: World, alpha: pd.DataFrame) -> None:
    frame = check_alpha_frame(alpha)
    assert set(frame.index) <= set(STOCKS) and len(frame) >= 30
    assert frame["confidence"].between(0, 1).all()
    assert frame["exp_return_ann"].abs().max() < 1.0  # annualised fractions, not percent
    assert np.sign(frame["exp_return_ann"]).equals(np.sign(frame["score"]))


def test_book_risk_agrees_between_the_model_and_the_metrics_library(
    world: World, alpha: pd.DataFrame
) -> None:
    model = RiskModelStore(Lake(world.lake.root), world.config).read_snapshot(AS_OF)
    assert model is not None
    ranked = alpha["score"].sort_values()
    longs, shorts = ranked.index[-8:], ranked.index[:8]
    weights = pd.concat(
        [pd.Series(0.05, index=longs), pd.Series(-0.05, index=shorts), pd.Series({"SPY": -0.02})]
    )  # NAV fractions: 40% long, 40% short, a small ETF hedge
    risk = portfolio_risk(model, weights)
    assert risk.total_var == pytest.approx(risk.factor_var + risk.specific_var, rel=1e-12)
    assert risk.by_factor.sum() == pytest.approx(risk.factor_var, rel=1e-9)
    assert risk.contribution.sum() == pytest.approx(risk.total_vol, rel=1e-9)
    euler = risk_contributions(weights, model)
    assert euler.vol == pytest.approx(risk.total_vol, rel=1e-12)
    assert euler.component.sum() == pytest.approx(euler.vol, rel=1e-9)
    var = portfolio_var(weights, cov=model.cov(list(weights.index)), method="normal")
    assert 0 < var.var < var.cvar < 0.05  # daily loss fractions of NAV
    assert var.var == pytest.approx(1.6448536269514722 * risk.total_vol, rel=1e-6)


def test_signal_tearsheet_is_scored_by_the_metrics_library(world: World) -> None:
    def factory(day: date) -> DataView:
        return world.view(day)

    sheet = research_signal(
        "ret_21d", factory, date(2023, 3, 1), date(2023, 11, 30), config=world.config
    )
    headline = sheet.summary["ic"][f"h{sheet.headline_horizon}"]["spearman"]
    assert headline["n_dates"] == 9 and abs(headline["mean"]) < 0.5
    returns = sheet.backtest.returns
    assert len(returns) > 150 and returns.notna().all()
    assert sheet.backtest.summary["sharpe"] == pytest.approx(
        sharpe_ratio(returns, config=world.config), rel=1e-12
    )
    summary = performance_summary(returns, config=world.config)
    assert np.isfinite(summary["annualised_vol"]) and summary["max_drawdown"] >= 0
