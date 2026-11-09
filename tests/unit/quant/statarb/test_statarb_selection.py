"""Stage 23 Task 1 (brief task 1): synthetic cointegrated pairs; the selection recovers the
planted pairs and rejects random walks (acceptance: false positives under 5%)."""

from datetime import date
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from statarb_world import (
    AS_OF,
    build_pairs_lake,
    ou_spread,
    planted_universe,
    random_walk_universe,
)

from atlas.core.config import AtlasConfig, StatArbConfig
from atlas.core.errors import LookAheadError
from atlas.core.pit import tag_label
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.data.view import DataView
from atlas.quant.statarb.selection import (
    Pair,
    coint_pvalue,
    evaluate_pair,
    hedge_ratio,
    log_price_panel,
    pair_id,
    select_pairs,
    select_pairs_at,
)

CFG = StatArbConfig()


def _window(u_log: pd.DataFrame, n: int = CFG.formation) -> pd.DataFrame:
    return u_log.iloc[-n:]


# --- generator -------------------------------------------------------------------------------


def test_ou_spread_has_the_planted_half_life() -> None:
    rng = np.random.default_rng(1)
    s = ou_spread(20_000, 10.0, 0.01, rng)
    phi = np.polyfit(s[:-1], s[1:], 1)[0]
    assert -np.log(2) / np.log(phi) == pytest.approx(10.0, rel=0.1)


def test_planted_universe_layout() -> None:
    u = planted_universe(3)
    assert len(u.planted) == 6 and u.cross is not None
    assert u.returns.shape == (600, 6 * 2 + 3 * 2 + 2)
    assert list(u.returns.columns) == sorted(u.returns.columns)
    # Returns rebuild the paths up to a constant.
    lp = log_price_panel(u.returns)
    diff = (lp - u.log_prices).iloc[1:]
    assert np.allclose(diff.sub(diff.iloc[0]), 0.0, atol=1e-9)
    corr = np.diff(u.log_prices["ENEN0"]).dot(np.diff(u.log_prices["ENEN1"]))
    assert corr > 0


# --- pure statistics -------------------------------------------------------------------------


def test_pair_id_is_alphabetical() -> None:
    assert pair_id("PEP", "KO") == "KO~PEP" == pair_id("KO", "PEP")
    with pytest.raises(ValueError, match="same"):
        pair_id("KO", "KO")


def test_hedge_ratio_recovers_beta() -> None:
    rng = np.random.default_rng(2)
    x = np.cumsum(rng.normal(0, 0.01, 504)) + 3
    y = 0.4 + 1.25 * x + ou_spread(504, 5.0, 0.005, rng)
    beta, alpha = hedge_ratio(y, x)
    assert beta == pytest.approx(1.25, abs=0.05)
    assert alpha == pytest.approx(0.4, abs=0.2)
    with pytest.raises(ValueError):
        hedge_ratio(y, x[:-1])


def test_coint_pvalue_small_for_planted_and_symmetric() -> None:
    u = planted_universe(4)
    y, x = sorted(next(iter(sorted(u.planted))).split("~"))
    lp = _window(u.log_prices)
    p = coint_pvalue(lp[y].to_numpy(), lp[x].to_numpy())
    assert p < 0.01
    assert p == coint_pvalue(lp[x].to_numpy(), lp[y].to_numpy())
    # Identical series: no test is possible (perfect fit) -> NaN, never a pass.
    assert np.isnan(coint_pvalue(lp[y].to_numpy(), lp[y].to_numpy()))


def test_evaluate_pair_reports_the_first_failed_filter() -> None:
    u = planted_universe(5)
    lp = _window(u.log_prices)
    a, b = sorted(next(iter(sorted(u.planted))).split("~"))
    ok = evaluate_pair(lp[a].to_numpy(), lp[b].to_numpy(), CFG)
    assert ok.failed is None and ok.passed
    assert ok.corr > CFG.min_corr and ok.pvalue < CFG.max_pvalue and ok.hedge_ratio > 0
    assert CFG.min_half_life <= ok.half_life <= CFG.max_half_life and ok.hurst < CFG.max_hurst
    # Uncorrelated walks fail the correlation prefilter (and nothing else is computed).
    rng = np.random.default_rng(6)
    w1, w2 = (np.cumsum(rng.normal(0, 0.01, 504)) for _ in range(2))
    res = evaluate_pair(w1, w2, CFG)
    assert res.failed == "corr" and np.isnan(res.pvalue)
    # Tighter thresholds fail at the next filters in order.
    assert (
        evaluate_pair(
            lp[a].to_numpy(), lp[b].to_numpy(), CFG.model_copy(update={"min_corr": 0.999})
        ).failed
        == "corr"
    )
    assert evaluate_pair(
        lp[a].to_numpy(), -lp[b].to_numpy() + 10, CFG.model_copy(update={"min_corr": 0.01})
    ).failed in {"corr", "hedge_ratio"}
    assert (
        evaluate_pair(
            lp[a].to_numpy(),
            lp[b].to_numpy(),
            CFG.model_copy(update={"max_half_life": ok.half_life * 0.5, "min_half_life": 0.1}),
        ).failed
        == "half_life"
    )
    assert (
        evaluate_pair(
            lp[a].to_numpy(), lp[b].to_numpy(), CFG.model_copy(update={"max_hurst": ok.hurst * 0.5})
        ).failed
        == "hurst"
    )
    # A half-life below the lower bound fails too (mutation check, 2026-11-09).
    low = CFG.model_copy(update={"min_half_life": ok.half_life * 1.5})
    assert evaluate_pair(lp[a].to_numpy(), lp[b].to_numpy(), low).failed == "half_life"


def test_evaluate_pair_negative_hedge_ratio_fails() -> None:
    rng = np.random.default_rng(7)
    x = np.cumsum(rng.normal(0, 0.01, 504)) + 3
    y = 5 - 0.8 * x + ou_spread(504, 4.0, 0.002, rng)  # cointegrated, but beta < 0
    cfg = CFG.model_copy(update={"min_corr": 0.01})
    # Returns are negatively correlated: the prefilter (corr > min_corr) rejects it first.
    assert evaluate_pair(y, x, cfg).failed == "corr"
    res = evaluate_pair(y, x, cfg, check_corr=False)
    assert res.failed == "hedge_ratio" and res.hedge_ratio < 0


def test_evaluate_pair_needs_equal_lengths() -> None:
    with pytest.raises(ValueError):
        evaluate_pair(np.zeros(504), np.zeros(503), CFG)


# --- acceptance: recovery and false positives ------------------------------------------------


def test_planted_pairs_are_recovered() -> None:
    """Acceptance (plan 17): every planted pair (half-life 5 and 10 sessions) is selected on at
    least 95% of 40 seeds; the cross-sector planted pair never is."""
    found: dict[float, int] = {5.0: 0, 10.0: 0}
    total = 0
    for seed in range(40):
        u = planted_universe(seed)
        pairs = select_pairs(_window(u.log_prices), u.sectors, config=CFG)
        ids = {p.pair_id for p in pairs}
        assert u.cross not in ids
        for pid in u.planted:
            hl = 5.0 if "Y0" in pid else 10.0
            found[hl] += pid in ids
        total += 1
    for hl, hits in found.items():
        assert hits / (3 * total) >= 0.95, (hl, hits)


def test_cross_sector_pair_passes_the_filters_but_is_not_paired() -> None:
    u = planted_universe(11)
    lp = _window(u.log_prices)
    assert u.cross is not None
    a, b = u.cross.split("~")
    assert evaluate_pair(lp[a].to_numpy(), lp[b].to_numpy(), CFG).passed
    one_sector = u.sectors.copy()
    one_sector[b] = one_sector[a]
    assert u.cross in {p.pair_id for p in select_pairs(lp, one_sector, config=CFG)}
    assert u.cross not in {p.pair_id for p in select_pairs(lp, u.sectors, config=CFG)}


def test_false_positives_on_random_walks_under_five_percent() -> None:
    """Acceptance (plan 17): correlated random walks (market factor + own walk, daily return
    correlation ~0.86, so they pass the 0.7 prefilter) are not cointegrated: under 5% of
    them may pass every filter. Estimated on 3000 *independent* pairs (one per 2-name
    universe, seeds 100000..102999, the sample of `artifacts/fp_rate_probe.py`): the first
    design (420 pairs from 28 six-name universes) shared names across pairs, so its pairs were
    dependent and its rate too noisy to test a 5% bound (worklog, 2026-11-09)."""
    n = 3000
    passed = prefiltered = 0
    for seed in range(100_000, 100_000 + n):
        lp = _window(random_walk_universe(seed, n_names=2).log_prices)
        res = evaluate_pair(lp["RW00"].to_numpy(), lp["RW01"].to_numpy(), CFG)
        prefiltered += res.failed != "corr"
        passed += res.passed
    assert prefiltered / n > 0.95  # the prefilter alone does not do the work
    rate = passed / n
    assert rate < 0.05, rate
    # The bound holds with margin: rate + 2 binomial standard errors is still below 5%.
    assert rate + 2 * np.sqrt(rate * (1 - rate) / n) < 0.05, rate


def test_selection_only_keeps_pairs_that_pass_every_filter() -> None:
    """Caps only remove: on multi-name random-walk universes each selected pair passes every
    filter, and the selection is never larger than the passing set."""
    for seed in range(1000, 1010):
        u = random_walk_universe(seed, n_names=6)
        lp = _window(u.log_prices)
        passing = {
            f"{a}~{b}"
            for a, b in combinations(lp.columns, 2)
            if evaluate_pair(lp[a].to_numpy(), lp[b].to_numpy(), CFG).passed
        }
        chosen = {p.pair_id for p in select_pairs(lp, u.sectors, config=CFG)}
        assert chosen <= passing


# --- selection rules -------------------------------------------------------------------------


def test_select_pairs_fields_ranking_and_determinism() -> None:
    u = planted_universe(21)
    lp = _window(u.log_prices)
    pairs = select_pairs(lp, u.sectors, config=CFG, formed=date(2024, 3, 1))
    assert pairs == select_pairs(lp, u.sectors, config=CFG, formed=date(2024, 3, 1))
    keys = [(p.pvalue, p.half_life, p.pair_id) for p in pairs]
    assert keys == sorted(keys)
    for p in pairs:
        assert isinstance(p, Pair)
        assert p.y < p.x and p.pair_id == f"{p.y}~{p.x}"
        assert u.sectors[p.y] == u.sectors[p.x] == p.sector
        assert p.hedge_ratio > 0 and p.pvalue < CFG.max_pvalue and p.corr > CFG.min_corr
        assert p.formed == date(2024, 3, 1)
        assert CFG.min_half_life <= p.half_life <= CFG.max_half_life and p.hurst < 0.5
        beta, alpha = hedge_ratio(lp[p.y].to_numpy(), lp[p.x].to_numpy())
        assert (p.hedge_ratio, p.intercept) == pytest.approx((beta, alpha))


def test_caps_per_name_and_total() -> None:
    # One sector of many planted pairs sharing legs: x0 is cointegrated with y0, y1, y2.
    rng = np.random.default_rng(31)
    n = 504
    x = np.cumsum(rng.normal(0, 0.01, n)) + 3
    paths = {"AX": x}
    for k, sym in enumerate(("BY", "CY", "DY")):
        paths[sym] = 0.1 * k + x + ou_spread(n, 4.0 + k, 0.006, rng)
    lp = pd.DataFrame(paths)
    sectors = pd.Series("Energy", index=lp.columns, dtype="object")
    pairs = select_pairs(lp, sectors, config=CFG)
    count = pd.Series([s for p in pairs for s in (p.y, p.x)]).value_counts()
    assert count.max() <= 2
    capped = select_pairs(lp, sectors, config=CFG.model_copy(update={"max_pairs": 1}))
    assert len(capped) == 1 and capped[0] == pairs[0]
    one = select_pairs(lp, sectors, config=CFG.model_copy(update={"max_pairs_per_name": 1}))
    count1 = pd.Series([s for p in one for s in (p.y, p.x)]).value_counts()
    assert count1.max() == 1


def test_names_without_sector_or_full_window_are_not_candidates() -> None:
    u = planted_universe(41)
    lp = _window(u.log_prices).copy()
    pid = sorted(u.planted)[0]
    a, b = pid.split("~")
    assert pid in {p.pair_id for p in select_pairs(lp, u.sectors, config=CFG)}
    no_sector = u.sectors.copy()
    no_sector[a] = None
    assert pid not in {p.pair_id for p in select_pairs(lp, no_sector, config=CFG)}
    missing = u.sectors.drop(a)
    assert pid not in {p.pair_id for p in select_pairs(lp, missing, config=CFG)}
    gap = lp.copy()
    gap.iloc[100, gap.columns.get_loc(b)] = np.nan
    assert pid not in {p.pair_id for p in select_pairs(gap, u.sectors, config=CFG)}


def test_select_pairs_uses_only_the_last_formation_sessions() -> None:
    u = planted_universe(51)
    longer = u.log_prices  # 600 sessions
    assert select_pairs(longer, u.sectors, config=CFG) == select_pairs(
        _window(longer), u.sectors, config=CFG
    )
    # Garbage before the window changes nothing.
    noisy = longer.copy()
    noisy.iloc[:50] = np.nan
    assert select_pairs(noisy, u.sectors, config=CFG) == select_pairs(
        _window(longer), u.sectors, config=CFG
    )
    with pytest.raises(ValueError, match="formation"):
        select_pairs(longer.iloc[-100:], u.sectors, config=CFG)


def test_select_pairs_rejects_labels() -> None:
    u = planted_universe(52)
    with pytest.raises(LookAheadError):
        select_pairs(tag_label(_window(u.log_prices)), u.sectors, config=CFG)


def test_empty_and_tiny_universes() -> None:
    u = planted_universe(53)
    lp = _window(u.log_prices)
    assert select_pairs(lp.iloc[:, :1], u.sectors, config=CFG) == []
    assert select_pairs(lp.iloc[:, :0], u.sectors, config=CFG) == []


def test_log_price_panel_keeps_gaps() -> None:
    r = pd.DataFrame({"A": [0.1, np.nan, 0.1, 0.0]})
    lp = log_price_panel(r)
    assert np.isnan(lp.iloc[1, 0])
    assert lp.iloc[0, 0] == pytest.approx(np.log(1.1))
    assert lp.iloc[2, 0] == pytest.approx(2 * np.log(1.1))


# --- DataView loader -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def lake_world(tmp_path_factory: pytest.TempPathFactory) -> tuple[Lake, object]:
    root: Path = tmp_path_factory.mktemp("statarb") / "lake"
    lake = Lake(root, registry=DEFAULT_REGISTRY.copy())
    u = planted_universe(61)
    build_pairs_lake(lake, u)
    return lake, u


def test_select_pairs_at_reads_the_view_point_in_time(lake_world: tuple[Lake, object]) -> None:
    lake, u = lake_world
    view = DataView(lake, AS_OF, AtlasConfig())
    pairs = select_pairs_at(view)
    rets = view.returns(list(u.sectors.index), CFG.formation)  # type: ignore[attr-defined]
    expected = select_pairs(
        log_price_panel(rets),
        u.sectors,
        config=CFG,
        formed=AS_OF,  # type: ignore[attr-defined]
    )
    assert pairs == expected and pairs
    assert set(u.planted) <= {p.pair_id for p in pairs}  # type: ignore[attr-defined]
    assert all(p.formed == AS_OF for p in pairs)


def test_select_pairs_at_ignores_bars_after_the_as_of(lake_world: tuple[Lake, object]) -> None:
    lake, u = lake_world
    early = date(2024, 1, 2)
    view = DataView(lake, early, AtlasConfig())
    pairs = select_pairs_at(view)
    rets = u.returns.loc[: pd.Timestamp(date(2023, 12, 29))]  # type: ignore[attr-defined]
    expected = select_pairs(
        log_price_panel(rets).iloc[-CFG.formation :],
        u.sectors,  # type: ignore[attr-defined]
        config=CFG,
        formed=early,
    )
    assert [p.pair_id for p in pairs] == [p.pair_id for p in expected]
    assert [p.hedge_ratio for p in pairs] == pytest.approx([p.hedge_ratio for p in expected])


def test_select_pairs_at_with_no_members(tmp_path: Path) -> None:
    lake = Lake(tmp_path / "lake", registry=DEFAULT_REGISTRY.copy())
    u = planted_universe(62)
    build_pairs_lake(lake, u, end_date=date(2015, 1, 2))
    assert select_pairs_at(DataView(lake, AS_OF, AtlasConfig())) == []
