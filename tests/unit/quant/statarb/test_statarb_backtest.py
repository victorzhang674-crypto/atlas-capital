"""Stage 23 Task 4 (brief task 4): the vectorised pairs backtest (Stage 17 engine, costs)."""

from collections.abc import Sequence
from datetime import date

import numpy as np
import pandas as pd
import pytest
from statarb_world import Universe, planted_universe

from atlas.core.config import StatArbConfig
from atlas.core.errors import LookAheadError
from atlas.core.pit import tag_label
from atlas.quant.research import weights_backtest
from atlas.quant.statarb.backtest import (
    TRADE_COLUMNS,
    PairsBacktestResult,
    pairs_backtest,
)
from atlas.quant.statarb.selection import coint_pvalue, log_price_panel
from atlas.quant.statarb.signals import Event, latest_zscore, zscore_lookback

CFG = StatArbConfig()


@pytest.fixture(scope="module")
def world() -> Universe:
    return planted_universe(91, n=820, noise_names=1)


def _bounds(u: Universe, first: int = 530) -> tuple[date, date]:
    idx = u.returns.index
    return idx[first].date(), idx[-1].date()


@pytest.fixture(scope="module")
def result(world: Universe) -> PairsBacktestResult:
    start, end = _bounds(world)
    return pairs_backtest(world.returns, world.sectors, start=start, end=end, config=CFG)


def test_planted_pairs_make_money_gross(result: PairsBacktestResult, world: Universe) -> None:
    bt = result.backtest
    assert bt.gross_returns.sum() > 0
    assert result.summary["n_trades"] >= 10
    assert result.summary["hit_rate"] > 0.5
    selected = set(result.selections["pair_id"])
    assert world.planted <= selected
    assert world.cross not in selected


def test_costs_are_turnover_times_bps(result: PairsBacktestResult) -> None:
    bt = result.backtest
    np.testing.assert_allclose(bt.returns, bt.gross_returns - bt.costs)
    per_rebalance = bt.costs.loc[bt.turnover.index]
    np.testing.assert_allclose(per_rebalance, bt.turnover * CFG.cost_bps / 1e4)
    assert bt.costs.sum() > 0
    assert result.summary["cost_bps"] == CFG.cost_bps


def test_engine_is_the_stage17_weights_backtest(
    result: PairsBacktestResult, world: Universe
) -> None:
    bt = result.backtest
    end = bt.returns.index[-1]
    again = weights_backtest(
        bt.weights, world.returns.loc[:end], cost_bps=CFG.cost_bps, sessions_per_year=252
    )
    pd.testing.assert_series_equal(again.returns, bt.returns)


def test_weights_are_dollar_neutral_and_capped(result: PairsBacktestResult) -> None:
    w = result.backtest.weights
    np.testing.assert_allclose(w.sum(axis=1), 0.0, atol=1e-12)
    gross = w.abs().sum(axis=1)
    assert (gross <= CFG.max_pairs * CFG.pair_gross + 1e-12).all()
    assert result.daily["open_pairs"].max() <= CFG.max_pairs
    assert (
        np.isclose(gross, result.daily["open_pairs"] * CFG.pair_gross)
        | (gross < result.daily["open_pairs"] * CFG.pair_gross)
    ).all()  # legs may net across pairs
    per_name = result.daily["max_pairs_per_name"]
    assert per_name.max() <= CFG.max_pairs_per_name


def test_trade_log(result: PairsBacktestResult, world: Universe) -> None:
    t = result.trades
    assert list(t.columns) == list(TRADE_COLUMNS)
    closed = t[t["exit"].notna()]
    assert set(closed["exit_event"]) <= {
        str(e)
        for e in (Event.EXIT, Event.STOP_LOSS, Event.TIME_STOP, Event.BREAKDOWN, Event.DESELECTED)
    }
    assert (closed["exit"] > closed["entry"]).all()
    assert (closed["held"] >= 1).all()
    assert set(t["side"]) <= {"long_spread", "short_spread"}
    for row in closed.itertuples():
        assert (row.long, row.short) == (
            (row.y, row.x) if row.side == "long_spread" else (row.x, row.y)
        )
    assert result.summary["n_trades"] == len(t)
    lp = log_price_panel(world.returns)
    for row in closed.itertuples():  # (R_long - R_short) / 2 from entry close to exit close
        e, x = pd.Timestamp(row.entry), pd.Timestamp(row.exit)
        r_long = np.expm1(lp.loc[x, row.long] - lp.loc[e, row.long])
        r_short = np.expm1(lp.loc[x, row.short] - lp.loc[e, row.short])
        assert row.gross_return == pytest.approx(0.5 * (r_long - r_short))
        k_e, k_x = world.returns.index.get_loc(e), world.returns.index.get_loc(x)
        assert row.held == k_x - k_e
    wins = (closed["gross_return"] > 0).mean()
    assert result.summary["hit_rate"] == pytest.approx(wins)


def test_open_pairs_keep_their_entry_parameters_across_formations(
    result: PairsBacktestResult, world: Universe
) -> None:
    """A trade spanning a formation date (not deselected) keeps the parameters it entered
    with: its `formed` is the formation before its entry."""
    formations = sorted(set(result.selections["formed"]))
    lp = log_price_panel(world.returns)
    idx = world.returns.index
    spanning = 0
    for row in result.trades.itertuples():
        crossed = [f for f in formations if row.entry < f and (pd.isna(row.exit) or f <= row.exit)]
        if crossed and row.exit_event != "deselected":
            spanning += 1
            assert row.formed <= row.entry and row.formed < crossed[0]
            if not pd.isna(row.exit):  # the exit z is computed with the entry parameters
                k = idx.get_loc(pd.Timestamp(row.exit))
                n = zscore_lookback(row.half_life, CFG)
                s = lp[row.y].iloc[k - n : k] - row.hedge_ratio * lp[row.x].iloc[k - n : k]
                z, _ = latest_zscore(s.to_numpy(), n)
                assert row.exit_z == pytest.approx(z)
        prior = [f for f in formations if f <= row.entry]
        assert row.formed == prior[-1]
    assert spanning >= 1


def test_breakdown_timing(world: Universe) -> None:
    """With a strict breakdown threshold, every breakdown exit happens on a re-test session
    (a multiple of `breakdown_every` after its formation) whose window, ending the session
    before, has an Engle-Granger p-value above the threshold."""
    cfg = CFG.model_copy(update={"breakdown_pvalue": 0.051})
    start, end = _bounds(world)
    out = pairs_backtest(world.returns, world.sectors, start=start, end=end, config=cfg)
    broken = out.trades[out.trades["exit_event"] == "breakdown"]
    assert len(broken) >= 3
    idx = world.returns.index
    lp = log_price_panel(world.returns)
    formations = sorted(set(out.selections["formed"]))
    for row in broken.itertuples():
        k = idx.get_loc(pd.Timestamp(row.exit))
        formed = max(f for f in formations if f <= row.exit)
        assert (k - idx.get_loc(pd.Timestamp(formed))) % cfg.breakdown_every == 0
        window = slice(k - cfg.formation, k)
        p = coint_pvalue(lp[row.y].iloc[window].to_numpy(), lp[row.x].iloc[window].to_numpy())
        assert p > cfg.breakdown_pvalue


def test_no_look_ahead(world: Universe) -> None:
    """Targets at session t use closes through t-1: changing the returns of t onwards leaves
    the targets up to and including t unchanged."""
    start, _ = _bounds(world)
    idx = world.returns.index
    end = idx[620].date()
    base = pairs_backtest(world.returns, world.sectors, start=start, end=end, config=CFG)
    cut = 600
    shocked = world.returns.copy()
    rng = np.random.default_rng(5)
    shocked.iloc[cut:] = rng.normal(0, 0.05, shocked.iloc[cut:].shape)
    after = pairs_backtest(shocked, world.sectors, start=start, end=end, config=CFG)
    t = idx[cut]
    pd.testing.assert_frame_equal(base.backtest.weights.loc[:t], after.backtest.weights.loc[:t])
    later_base = base.backtest.weights.loc[t:].iloc[1:]
    assert not later_base.equals(after.backtest.weights.loc[t:].iloc[1:])


def test_no_look_ahead_in_the_monthly_selection(world: Universe) -> None:
    """A formation on session t selects on closes through t-1 (mutation check, 2026-11-09):
    shocking the returns from a formation date on leaves that formation's pairs, hedge ratios
    and p-values, and the targets up to that date, unchanged."""
    start, _ = _bounds(world)
    idx = world.returns.index
    end = idx[700].date()
    base = pairs_backtest(world.returns, world.sectors, start=start, end=end, config=CFG)
    formed = sorted(set(base.selections["formed"]))
    t = pd.Timestamp(formed[2])
    shocked = world.returns.copy()
    shocked.loc[t:] = np.random.default_rng(6).normal(0, 0.05, shocked.loc[t:].shape)
    after = pairs_backtest(shocked, world.sectors, start=start, end=end, config=CFG)
    upto = base.selections[base.selections["formed"] <= formed[2]].reset_index(drop=True)
    upto_after = after.selections[after.selections["formed"] <= formed[2]].reset_index(drop=True)
    pd.testing.assert_frame_equal(upto, upto_after)
    cols = base.backtest.weights.columns.union(after.backtest.weights.columns)
    w0 = base.backtest.weights.reindex(columns=cols, fill_value=0.0).loc[:t]
    w1 = after.backtest.weights.reindex(columns=cols, fill_value=0.0).loc[:t]
    pd.testing.assert_frame_equal(w0, w1)
    later = base.selections[base.selections["formed"] > formed[2]].reset_index(drop=True)
    later_after = after.selections[after.selections["formed"] > formed[2]].reset_index(drop=True)
    assert not later.equals(later_after)


def test_deselected_pairs_are_closed(world: Universe) -> None:
    """A name leaving the universe at a formation: its open pairs close at that formation."""
    start, end = _bounds(world)
    base = pairs_backtest(world.returns, world.sectors, start=start, end=end, config=CFG)
    open_at = base.trades[base.trades["exit_event"] != "deselected"]
    formations = sorted(set(base.selections["formed"]))
    # find a trade open across a formation; drop one of its legs from that formation on
    for row in open_at.itertuples():
        nxt = [f for f in formations if row.entry < f and (pd.isna(row.exit) or f <= row.exit)]
        if nxt:
            boundary, leaver = nxt[0], row.y
            break
    else:
        pytest.fail("no trade spans a formation")

    def members(day: date) -> Sequence[str]:
        names = list(world.sectors.index)
        return [n for n in names if not (n == leaver and day >= boundary)]

    out = pairs_backtest(
        world.returns, world.sectors, start=start, end=end, config=CFG, members=members
    )
    closed = out.trades[(out.trades["pair_id"] == row.pair_id) & (out.trades["entry"] == row.entry)]
    assert len(closed) == 1
    assert closed["exit_event"].iloc[0] == "deselected" and closed["exit"].iloc[0] == boundary
    after = out.backtest.weights.loc[pd.Timestamp(boundary) :]
    assert (after[leaver] == 0).all()
    assert leaver not in set(out.selections.loc[out.selections["formed"] >= boundary, "y"])


def test_too_little_history_means_no_pairs(world: Universe) -> None:
    idx = world.returns.index
    early = pairs_backtest(
        world.returns, world.sectors, start=idx[100].date(), end=idx[140].date(), config=CFG
    )
    assert early.selections.empty and early.trades.empty
    assert (early.backtest.weights == 0).all().all()
    assert early.summary["n_formations"] == 0


def test_progress_called_once_per_formation(world: Universe) -> None:
    idx = world.returns.index
    seen: list[tuple[date, int]] = []
    out = pairs_backtest(world.returns, world.sectors, start=idx[600].date(),
                         end=idx[660].date(), config=CFG,
                         progress=lambda day, n: seen.append((day, n)))  # fmt: skip
    formed = out.selections.groupby("formed").size()
    assert seen == [(d, int(formed.get(d, 0))) for d, _ in seen]
    assert [d for d, _ in seen] == sorted(set(out.selections["formed"]))
    assert len(seen) == out.summary["n_formations"]


def test_deterministic(world: Universe, result: PairsBacktestResult) -> None:
    start, end = _bounds(world)
    again = pairs_backtest(world.returns, world.sectors, start=start, end=end, config=CFG)
    pd.testing.assert_frame_equal(again.trades, result.trades)
    pd.testing.assert_series_equal(again.backtest.returns, result.backtest.returns)


def test_argument_checks(world: Universe) -> None:
    idx = world.returns.index
    with pytest.raises(ValueError, match="start"):
        pairs_backtest(
            world.returns, world.sectors, start=idx[600].date(), end=idx[500].date(), config=CFG
        )
    with pytest.raises(ValueError, match="no session"):
        pairs_backtest(
            world.returns, world.sectors, start=date(2030, 1, 1), end=date(2030, 2, 1), config=CFG
        )
    with pytest.raises(LookAheadError):
        pairs_backtest(
            tag_label(world.returns),
            world.sectors,
            start=idx[600].date(),
            end=idx[610].date(),
            config=CFG,
        )
