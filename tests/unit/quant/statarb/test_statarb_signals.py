"""Stage 23 Task 3 (brief task 3): the signal state machine (flat → long/short spread →
exit / stop), z-scores, breakdown, `PairSignal` and dollar-neutral weights."""

import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError
from statarb_world import AS_OF, build_pairs_lake, planted_universe

from atlas.core.config import AtlasConfig, StatArbConfig
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.data.view import DataView
from atlas.quant.statarb.selection import Pair, coint_pvalue, log_price_panel, select_pairs_at
from atlas.quant.statarb.signals import (
    Event,
    PairSignal,
    PairState,
    SpreadState,
    confidence,
    expected_return,
    latest_zscore,
    pairs_snapshot,
    rolling_coint_pvalue,
    run_pair,
    spread,
    step,
    target_weights,
    to_signal,
    zscore,
    zscore_lookback,
)

CFG = StatArbConfig()
HL = 5.0  # time stop after 15 sessions held
FLAT, LONG, SHORT = SpreadState.FLAT, SpreadState.LONG, SpreadState.SHORT


def _step(state: PairState, z: float, broken: bool = False) -> tuple[PairState, Event | None]:
    return step(state, z, broken=broken, half_life=HL, config=CFG)


def _pair(**kw: object) -> Pair:
    base = dict(
        pair_id="AAA~BBB",
        y="AAA",
        x="BBB",
        sector="Energy",
        hedge_ratio=0.8,
        intercept=0.1,
        half_life=HL,
        hurst=0.3,
        pvalue=0.01,
        corr=0.9,
    )
    base.update(kw)
    return Pair(**base)  # type: ignore[arg-type]


# --- z-scores --------------------------------------------------------------------------------


def test_zscore_lookback() -> None:
    assert zscore_lookback(5.0, CFG) == 10
    assert zscore_lookback(2.0, CFG) == 5  # 4 -> min_lookback
    assert zscore_lookback(7.25, CFG) == 15  # round half up: 14.5 -> 15
    assert zscore_lookback(60.0, CFG) == 120


def test_spread_and_zscore() -> None:
    y = pd.Series([1.0, 2.0, 3.0, 5.0])
    x = pd.Series([0.5, 1.0, 1.5, 2.0])
    s = spread(y, x, 2.0)
    assert list(s) == [0.0, 0.0, 0.0, 1.0]
    vals = pd.Series([1.0, 2.0, 3.0, 4.0, 10.0])
    z = zscore(vals, 3)
    assert z.iloc[:2].isna().all()
    window = np.array([2.0, 3.0, 4.0])
    assert z.iloc[3] == pytest.approx((4.0 - window.mean()) / window.std(ddof=1))
    window = np.array([3.0, 4.0, 10.0])
    assert z.iloc[4] == pytest.approx((10.0 - window.mean()) / window.std(ddof=1))
    assert np.isnan(zscore(pd.Series([1.0] * 5), 3).iloc[-1])  # zero variance -> NaN
    with pytest.raises(ValueError):
        zscore(vals, 1)


def test_latest_zscore_is_the_last_rolling_value() -> None:
    rng = np.random.default_rng(3)
    vals = pd.Series(rng.normal(size=40))
    for n in (2, 5, 17):
        z, sigma = latest_zscore(vals.to_numpy(), n)
        assert z == pytest.approx(zscore(vals, n).iloc[-1])
        assert sigma == pytest.approx(vals.iloc[-n:].std(ddof=1))
    assert all(math.isnan(v) for v in latest_zscore(np.ones(3), 5))  # too short
    assert math.isnan(latest_zscore(np.r_[np.nan, np.ones(4) * 2.0, 3.0], 5)[0]) is False
    assert math.isnan(latest_zscore(np.r_[1.0, np.nan, 2.0, 3.0], 3)[0])
    z, sigma = latest_zscore(np.ones(6), 4)
    assert math.isnan(z) and sigma == 0.0
    with pytest.raises(ValueError):
        latest_zscore(np.ones(6), 1)


# --- state machine: entries ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("z", "state", "event"),
    [
        (-2.5, LONG, Event.ENTER_LONG),
        (-4.0, LONG, Event.ENTER_LONG),  # |z| = stop exactly: not beyond the stop
        (2.5, SHORT, Event.ENTER_SHORT),
        (4.0, SHORT, Event.ENTER_SHORT),
        (-2.0, FLAT, None),  # exactly the entry threshold: no entry (strict)
        (2.0, FLAT, None),
        (0.0, FLAT, None),
        (-4.01, FLAT, None),  # beyond the stop: never enter
        (4.01, FLAT, None),
        (math.nan, FLAT, None),
    ],
)
def test_flat_transitions(z: float, state: SpreadState, event: Event | None) -> None:
    new, ev = _step(PairState(), z)
    assert new.state is state and ev == event
    assert new.held == 0 and not new.locked


def test_locked_pair_never_enters() -> None:
    locked = PairState(locked=True)
    assert _step(locked, -3.0) == (locked, None)
    assert _step(locked, 3.0) == (locked, None)


def test_breakdown_locks_a_flat_pair_once() -> None:
    new, ev = _step(PairState(), -3.0, broken=True)
    assert new == PairState(locked=True) and ev is Event.BREAKDOWN
    again, ev2 = _step(new, -3.0, broken=True)
    assert again == new and ev2 is None


# --- state machine: in a position ------------------------------------------------------------


@pytest.mark.parametrize(
    ("side", "z", "event", "locked"),
    [
        (LONG, -0.49, Event.EXIT, False),
        (LONG, -0.5, None, False),  # |z| = exit exactly: still open (strict)
        (LONG, 0.0, Event.EXIT, False),
        (LONG, 3.0, Event.EXIT, False),  # overshoot through zero: take the profit
        (LONG, -3.9, None, False),
        (LONG, -4.0, None, False),  # |z| = stop exactly: not a stop (strict)
        (LONG, -4.01, Event.STOP_LOSS, True),
        (SHORT, 0.49, Event.EXIT, False),
        (SHORT, 0.5, None, False),
        (SHORT, -3.0, Event.EXIT, False),
        (SHORT, 4.0, None, False),
        (SHORT, 4.01, Event.STOP_LOSS, True),
    ],
)
def test_position_transitions(
    side: SpreadState, z: float, event: Event | None, locked: bool
) -> None:
    new, ev = _step(PairState(side, held=3), z)
    assert ev == event
    if event is None:
        assert new == PairState(side, held=4)
    else:
        assert new == PairState(FLAT, held=0, locked=locked)


def test_breakdown_closes_and_locks() -> None:
    new, ev = _step(PairState(LONG, held=2), -2.5, broken=True)
    assert new == PairState(locked=True) and ev is Event.BREAKDOWN


def test_time_stop_after_three_half_lives() -> None:
    limit = CFG.time_stop_mult * HL  # 15 sessions
    new, ev = _step(PairState(SHORT, held=int(limit) - 2), 2.5)
    assert ev is None and new.held == int(limit) - 1
    new, ev = _step(new, 2.5)
    assert ev is Event.TIME_STOP and new == PairState(locked=True)
    # Fractional limit: 3 x 4.5 = 13.5 -> stops once held >= 13.5, i.e. at 14.
    st = PairState(LONG, held=12)
    st, ev = step(st, -2.5, broken=False, half_life=4.5, config=CFG)
    assert ev is None and st.held == 13
    _, ev = step(st, -2.5, broken=False, half_life=4.5, config=CFG)
    assert ev is Event.TIME_STOP


def test_priority_exit_then_stop_then_breakdown_then_time() -> None:
    at_limit = PairState(LONG, held=100)
    assert _step(at_limit, 0.0, broken=True)[1] is Event.EXIT
    assert _step(at_limit, -5.0, broken=True)[1] is Event.STOP_LOSS
    assert _step(at_limit, -3.0, broken=True)[1] is Event.BREAKDOWN
    assert _step(at_limit, -3.0, broken=False)[1] is Event.TIME_STOP


def test_nan_z_holds_but_time_stop_and_breakdown_apply() -> None:
    new, ev = _step(PairState(LONG, held=1), math.nan)
    assert ev is None and new == PairState(LONG, held=2)
    assert _step(PairState(LONG, held=1), math.nan, broken=True)[1] is Event.BREAKDOWN
    assert _step(PairState(LONG, held=14), math.nan)[1] is Event.TIME_STOP


def test_state_validation() -> None:
    with pytest.raises(ValueError):
        PairState(FLAT, held=3)
    with pytest.raises(ValueError):
        PairState(LONG, held=-1)
    with pytest.raises(ValueError):
        PairState(LONG, locked=True)
    with pytest.raises(ValueError):
        step(PairState(), 1.0, broken=False, half_life=0.0, config=CFG)


# --- run over a path -------------------------------------------------------------------------


def test_run_pair_scripted_path() -> None:
    idx = pd.date_range("2024-01-01", periods=12, freq="D")
    z = pd.Series([0, -2.1, -1.5, -0.3, 2.2, 3.0, 4.5, -2.5, -1.0, 0.0, 2.5, 0.4], index=idx)
    broken = pd.Series(False, index=idx)
    out = run_pair(z, broken, half_life=HL, config=CFG)
    assert list(out.columns) == ["z", "state", "event", "held", "locked"]
    assert list(out["state"]) == [
        FLAT,
        LONG,
        LONG,
        FLAT,
        SHORT,
        SHORT,
        FLAT,
        FLAT,
        FLAT,
        FLAT,
        FLAT,
        FLAT,
    ]
    assert list(out["event"]) == [
        None,
        Event.ENTER_LONG,
        None,
        Event.EXIT,
        Event.ENTER_SHORT,
        None,
        Event.STOP_LOSS,
        None,
        None,
        None,
        None,
        None,
    ]  # locked after the stop: z -2.5 and 2.5 do not re-enter
    assert list(out["held"]) == [0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0]
    assert out["locked"].iloc[6:].all() and not out["locked"].iloc[:6].any()


def test_run_pair_from_an_initial_state_and_breakdown() -> None:
    idx = pd.date_range("2024-01-01", periods=4, freq="D")
    z = pd.Series([-2.5, -2.4, -2.3, -2.2], index=idx)
    broken = pd.Series([False, False, True, True], index=idx)
    out = run_pair(z, broken, half_life=HL, config=CFG, initial=PairState(LONG, held=5))
    assert list(out["event"]) == [None, None, Event.BREAKDOWN, None]
    assert list(out["held"]) == [6, 7, 0, 0]
    with pytest.raises(ValueError):
        run_pair(z, broken.iloc[:3], half_life=HL, config=CFG)


# --- breakdown p-values ----------------------------------------------------------------------


def test_rolling_coint_pvalue_cadence_and_window() -> None:
    u = planted_universe(71)
    pid = sorted(u.planted)[0]
    a, b = pid.split("~")
    ly, lx = u.log_prices[a], u.log_prices[b]
    cfg = CFG.model_copy(update={"formation": 300, "breakdown_every": 7, "corr_window": 252})
    start = 400
    p = rolling_coint_pvalue(ly, lx, start=start, config=cfg)
    assert list(p.index) == list(ly.index[start:])
    tested = list(range(start, len(ly), 7))
    for i in tested[:3]:
        lo = i - 299
        expected = coint_pvalue(ly.iloc[lo : i + 1].to_numpy(), lx.iloc[lo : i + 1].to_numpy())
        assert p.iloc[i - start] == pytest.approx(expected)
    # carried forward between tests
    assert (p.iloc[1:7] == p.iloc[0]).all()
    with pytest.raises(ValueError, match="formation"):
        rolling_coint_pvalue(ly, lx, start=100, config=cfg)


# --- expected return, confidence, PairSignal, weights -----------------------------------------


def test_expected_return_known_values() -> None:
    # |z| 2, exit 0.5, sigma 0.04, beta 1 -> move 1.5 x 0.04 / 2 = 0.03 over 5 x log2(4) = 10
    # sessions -> annualised 0.03 x 252 / 10.
    er = expected_return(-2.0, sigma=0.04, hedge_ratio=1.0, half_life=5.0, config=CFG, periods=252)
    assert er == pytest.approx(0.03 * 252 / 10)
    assert expected_return(
        2.0, sigma=0.04, hedge_ratio=1.0, half_life=5.0, config=CFG, periods=252
    ) == pytest.approx(er)
    assert (
        expected_return(0.4, sigma=0.04, hedge_ratio=1.0, half_life=5.0, config=CFG, periods=252)
        == 0.0
    )
    assert (
        expected_return(0.5, sigma=0.04, hedge_ratio=1.0, half_life=5.0, config=CFG, periods=252)
        == 0.0
    )
    assert (
        expected_return(
            math.nan, sigma=0.04, hedge_ratio=1.0, half_life=5.0, config=CFG, periods=252
        )
        == 0.0
    )


def test_confidence_known_values() -> None:
    assert confidence(0.01, CFG) == pytest.approx(0.95)
    assert confidence(0.10, CFG) == pytest.approx(0.5)
    assert confidence(0.20, CFG) == 0.0
    assert confidence(0.5, CFG) == 0.0
    assert confidence(0.0, CFG) == 1.0
    assert confidence(math.nan, CFG) == 0.0


def test_pair_signal_validation() -> None:
    ok = PairSignal(
        pair_id="AAA~BBB",
        long="BBB",
        short="AAA",
        hedge_ratio=0.8,
        z=2.5,
        exp_return=0.3,
        confidence=0.9,
    )
    assert ok.long == "BBB"
    bad = dict(
        pair_id="AAA~BBB",
        long="AAA",
        short="BBB",
        hedge_ratio=0.8,
        z=-2.5,
        exp_return=0.3,
        confidence=0.9,
    )
    PairSignal(**bad)  # type: ignore[arg-type]
    for field, value in [
        ("long", "AAA2"),
        ("short", "AAA"),
        ("hedge_ratio", 0.0),
        ("z", math.nan),
        ("exp_return", -0.1),
        ("confidence", 1.1),
        ("confidence", -0.1),
        ("exp_return", math.inf),
    ]:
        with pytest.raises(ValidationError):
            PairSignal(**{**bad, field: value})  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        PairSignal(**bad, extra=1)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ok.z = 1.0  # type: ignore[misc]


def test_to_signal_orientation() -> None:
    pair = _pair()
    long_sig = to_signal(pair, LONG, z=-2.5, sigma=0.03, pvalue=0.02, config=CFG, periods=252)
    assert long_sig is not None
    assert (long_sig.long, long_sig.short) == ("AAA", "BBB")  # long y, short x
    assert long_sig.hedge_ratio == 0.8 and long_sig.z == -2.5
    assert long_sig.confidence == pytest.approx(0.9)
    assert long_sig.exp_return == pytest.approx(
        expected_return(-2.5, sigma=0.03, hedge_ratio=0.8, half_life=HL, config=CFG, periods=252)
    )
    short_sig = to_signal(pair, SHORT, z=2.5, sigma=0.03, pvalue=0.02, config=CFG, periods=252)
    assert short_sig is not None and (short_sig.long, short_sig.short) == ("BBB", "AAA")
    assert to_signal(pair, FLAT, z=2.5, sigma=0.03, pvalue=0.02, config=CFG, periods=252) is None


def test_target_weights_dollar_neutral_per_pair() -> None:
    s1 = PairSignal(
        pair_id="AAA~BBB",
        long="AAA",
        short="BBB",
        hedge_ratio=0.8,
        z=-2.5,
        exp_return=0.3,
        confidence=0.9,
    )
    s2 = PairSignal(
        pair_id="AAA~CCC",
        long="CCC",
        short="AAA",
        hedge_ratio=1.2,
        z=2.5,
        exp_return=0.3,
        confidence=0.9,
    )
    s3 = PairSignal(
        pair_id="DDD~EEE",
        long="DDD",
        short="EEE",
        hedge_ratio=1.0,
        z=-3.0,
        exp_return=0.3,
        confidence=0.9,
    )
    w = target_weights([s1, s2, s3], pair_gross=0.05)
    assert list(w.index) == ["AAA", "BBB", "CCC", "DDD", "EEE"]
    assert w["AAA"] == pytest.approx(0.0)  # +0.025 in one pair, -0.025 in the other
    assert w["BBB"] == pytest.approx(-0.025) and w["CCC"] == pytest.approx(0.025)
    assert w["DDD"] == pytest.approx(0.025) and w["EEE"] == pytest.approx(-0.025)
    assert w.sum() == pytest.approx(0.0)
    for sig in (s1, s2, s3):  # each pair alone is dollar-neutral with gross pair_gross
        alone = target_weights([sig], pair_gross=0.05)
        assert alone.sum() == pytest.approx(0.0) and alone.abs().sum() == pytest.approx(0.05)
    assert target_weights([], pair_gross=0.05).empty
    with pytest.raises(ValueError):
        target_weights([s1, s1], pair_gross=0.05)
    with pytest.raises(ValueError):
        target_weights([s1], pair_gross=0.0)


# --- snapshot through a DataView -------------------------------------------------------------


@pytest.fixture(scope="module")
def lake(tmp_path_factory: pytest.TempPathFactory) -> Lake:
    root: Path = tmp_path_factory.mktemp("statarb_sig") / "lake"
    lk = Lake(root, registry=DEFAULT_REGISTRY.copy())
    build_pairs_lake(lk, planted_universe(81))
    return lk


def test_pairs_snapshot(lake: Lake) -> None:
    view = DataView(lake, AS_OF, AtlasConfig())
    snap = pairs_snapshot(view)
    pairs = select_pairs_at(view)
    assert [p.pair_id for p in snap.pairs] == [p.pair_id for p in pairs]
    t = snap.table
    assert list(t["pair_id"]) == [p.pair_id for p in pairs]
    for col in (
        "sector",
        "y",
        "x",
        "hedge_ratio",
        "half_life",
        "hurst",
        "pvalue",
        "corr",
        "lookback",
        "z",
        "signal",
        "exp_return",
        "confidence",
    ):
        assert col in t.columns
    members = sorted(view.universe("sp500"))
    lp = log_price_panel(view.returns(members, CFG.formation))
    for p, row in zip(pairs, t.itertuples(), strict=True):
        n = zscore_lookback(p.half_life, CFG)
        z = zscore(spread(lp[p.y], lp[p.x], p.hedge_ratio), n).iloc[-1]
        assert row.z == pytest.approx(z) and row.lookback == n
        expected = "long_spread" if -4 <= z < -2 else "short_spread" if 2 < z <= 4 else "flat"
        assert row.signal == expected
    active = {s.pair_id for s in snap.signals}
    assert active == set(t.loc[t["signal"] != "flat", "pair_id"])
    for s in snap.signals:
        assert s.confidence == pytest.approx(
            confidence(float(t.set_index("pair_id").loc[s.pair_id, "pvalue"]), CFG)
        )
    assert snap.as_of == AS_OF


def test_pairs_snapshot_empty(tmp_path: Path) -> None:
    lk = Lake(tmp_path / "lake", registry=DEFAULT_REGISTRY.copy())
    build_pairs_lake(lk, planted_universe(82), end_date=date(2015, 1, 2))
    snap = pairs_snapshot(DataView(lk, AS_OF, AtlasConfig()))
    assert snap.pairs == [] and snap.signals == [] and snap.table.empty
    assert "pair_id" in snap.table.columns
