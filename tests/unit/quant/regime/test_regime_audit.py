"""Stage 21 Task 9 (self-audit): argument checks and the edge paths the first test pass left
uncovered (coverage audit)."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from regime_synthetic import regime_features
from regime_world import RegimeWorld, build_regime_world

from atlas.core.config import RegimeConfig, RegimeHMMConfig
from atlas.core.errors import DataError
from atlas.core.pit import AsOf
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.data.macro.pit import macro_vintages
from atlas.data.view import DataView
from atlas.quant.regime import (
    credit_proxy,
    filter_probabilities,
    fit_hmm,
    regime_at,
    regime_history,
    risk_scalar,
    trailing_zscore,
)
from atlas.quant.regime import loader as loader_module
from atlas.quant.regime import store as store_module
from atlas.quant.regime.hmm import _quiet_single_thread
from atlas.quant.regime.store import RegimeStore

CFG = RegimeConfig()


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> RegimeWorld:
    root = tmp_path_factory.mktemp("audit_world")
    return build_regime_world(Lake(root / "lake", registry=DEFAULT_REGISTRY.copy()))


# --- argument checks ---------------------------------------------------------------------------


def test_hmm_argument_checks() -> None:
    feats, _ = regime_features(300, 2, seed=1)
    with pytest.raises(TypeError):
        fit_hmm(feats.to_numpy(), 2, seed=0)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="n_init"):
        fit_hmm(feats, 2, seed=0, n_init=0)
    fit = fit_hmm(feats, 2, seed=0, n_init=1)
    with pytest.raises(ValueError, match="lack the columns"):
        filter_probabilities(fit, feats.drop(columns="slope"))
    with pytest.raises(TypeError):
        filter_probabilities(fit, feats.to_numpy())  # type: ignore[arg-type]


def test_credit_argument_checks() -> None:
    idx = pd.bdate_range("2020-01-01", periods=3)
    s = pd.Series([1.0, 2.0, 3.0], index=idx)
    with pytest.raises(ValueError, match="hedge_ratio"):
        credit_proxy(s, s, -0.1)
    with pytest.raises(ValueError, match="window"):
        trailing_zscore(s, 1)
    empty = credit_proxy(pd.Series(np.nan, index=idx), s, 0.5)
    assert empty["level"].isna().all() and empty["change"].isna().all()


def test_risk_scalar_rejects_unknown_trend_and_quadrant() -> None:
    with pytest.raises(ValueError, match="trend"):
        risk_scalar("LOW", "FLAT", stress=False, quadrant=None, p_turbulent=None, config=CFG)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="quadrant"):
        risk_scalar("LOW", "UP", stress=False, quadrant="Boom", p_turbulent=None, config=CFG)  # type: ignore[arg-type]


def test_macro_vintages_rejects_a_non_positive_lookback() -> None:
    frame = pd.DataFrame(
        columns=["series_id", "obs_date", "value", "realtime_start", "available_at"]
    )
    with pytest.raises(ValueError, match="lookback"):
        macro_vintages(frame, ["VIXCLS"], AsOf.for_session(date(2020, 3, 2)), timedelta(0))


# --- loader edge paths -------------------------------------------------------------------------


def test_no_macro_table_at_all_is_reported_not_raised(tmp_path: Path) -> None:
    w = build_regime_world(Lake(tmp_path / "lake", registry=DEFAULT_REGISTRY.copy()), macro=False)
    assert not w.lake.has_table("macro_observations")
    s = regime_at(DataView(w.lake, date(2019, 5, 6), w.config))
    assert s.vix is None and s.nfci is None and s.macro_quadrant is None
    assert {"vix", "macro_quadrant", "nfci"} <= set(s.missing)
    assert s.credit_source == "proxy"  # the price proxy still works


def test_trend_needs_the_200_session_average(world: RegimeWorld) -> None:
    """Mid-2017: the vol percentile already has its 120 observations, the 200-session
    average not yet."""
    with pytest.raises(DataError, match="trend regime"):
        regime_at(DataView(world.lake, date(2017, 8, 1), world.config))


def test_decisions_after_the_view_are_refused(world: RegimeWorld) -> None:
    view = DataView(world.lake, date(2019, 5, 6), world.config)
    with pytest.raises(ValueError, match="after the reading view"):
        loader_module._compute(view, [AsOf.for_session(date(2019, 5, 7))])


def test_a_decision_without_a_panel_row_raises(
    world: RegimeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loader_module, "latest_known_session", lambda *_: date(2019, 5, 4))
    with pytest.raises(DataError, match="no price panel rows"):
        regime_at(DataView(world.lake, date(2019, 5, 6), world.config))


def test_a_long_history_logs_progress(world: RegimeWorld) -> None:
    states = regime_history(world.lake, date(2019, 1, 2), date(2019, 12, 31), world.config)
    assert len(states) == 252
    assert all(s.data_date < s.as_of for s in states)


# --- store edge path ---------------------------------------------------------------------------


def test_rows_not_yet_known_are_invisible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stored row whose availability is after the reader's cutoff is never returned."""
    from test_regime_store import _state

    store = RegimeStore(Lake(tmp_path / "out", registry=DEFAULT_REGISTRY.copy()))
    far = datetime(2030, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(store_module, "bar_available_at", lambda *_: far)
    store.write([_state(date(2020, 3, 16))])
    assert store.latest(date(2020, 3, 16)) is None
    assert store.read(date(2020, 3, 1), date(2020, 3, 31), as_of=date(2020, 3, 31)) == []
    assert len(store.read(date(2020, 3, 1), date(2020, 3, 31))) == 1  # without an as-of


def test_hmm_config_used_by_history_is_the_regime_config() -> None:
    assert RegimeHMMConfig() == CFG.hmm


# --- mutation-check gaps (Task 9) ----------------------------------------------------------------


def test_breadth_counts_a_name_at_its_average_as_not_above() -> None:
    from atlas.quant.regime import breadth

    idx = pd.bdate_range("2020-01-01", periods=1)
    ratio = pd.DataFrame({"A": [1.0], "B": [1.2], "C": [0.9]}, index=idx)
    members = pd.DataFrame(True, index=idx, columns=ratio.columns)
    assert breadth(ratio, members, 1).iloc[0] == pytest.approx(1 / 3)


def test_transition_matrix_follows_the_state_order() -> None:
    """Calm persists longer than turbulence in the data; after ordering, transmat[0, 0] (calm)
    must exceed transmat[1, 1] whatever hmmlearn's internal state numbering was."""
    from hmmlearn.hmm import GaussianHMM

    feats, _ = regime_features(3000, 2, seed=8, stay=0.99)
    swapped = 0
    for seed in range(6):
        fit = fit_hmm(feats, 2, seed=seed, n_init=1)
        assert fit.transmat[0, 0] > fit.transmat[1, 1], seed
        raw = GaussianHMM(n_components=2, covariance_type="full", random_state=seed)
        with _quiet_single_thread():  # hmmlearn's own NumPy 2.5 deprecation
            raw.fit(((feats - fit.mean) / fit.scale).to_numpy())
        swapped += int(raw.means_[0, 1] > raw.means_[1, 1])
    assert swapped >= 1  # at least one seed numbered the turbulent state first


def test_fits_run_single_threaded(monkeypatch: pytest.MonkeyPatch) -> None:
    from hmmlearn.hmm import GaussianHMM
    from threadpoolctl import threadpool_info

    seen: list[int] = []
    original = GaussianHMM.fit

    def recording(self, x):  # type: ignore[no-untyped-def]
        seen.extend(int(lib["num_threads"]) for lib in threadpool_info())
        return original(self, x)

    monkeypatch.setattr(GaussianHMM, "fit", recording)
    feats, _ = regime_features(300, 2, seed=1)
    fit_hmm(feats, 2, seed=0, n_init=1)
    assert seen and set(seen) == {1}


def test_shorter_history_gives_the_same_states(world: RegimeWorld) -> None:
    """Truncation: the states of a history ending on day b equal the same days of a history
    ending later (rows after b never reach day b's state)."""
    short = regime_history(world.lake, date(2020, 2, 3), date(2020, 2, 21), world.config)
    long = regime_history(world.lake, date(2020, 2, 3), date(2020, 3, 31), world.config)
    assert long[: len(short)] == short


def test_history_uses_each_days_membership(world: RegimeWorld) -> None:
    """Membership changes on 2019-07-01 (two leave, two join): each day's breadth uses that
    day's members, in the history as in a one-day view."""
    hist = {
        s.as_of: s
        for s in regime_history(world.lake, date(2019, 6, 24), date(2019, 7, 12), world.config)
    }
    for day in (date(2019, 6, 26), date(2019, 6, 28), date(2019, 7, 2)):
        assert regime_at(DataView(world.lake, day, world.config)) == hist[day], day


def test_hmm_fit_uses_the_slope_known_at_the_month_start(tmp_path: Path) -> None:
    """A curve value revised mid-month (published 2019-05-15): the May fit still uses the
    slope known on May's first session, in a one-day view on 2019-05-20 as in the history."""
    from regime_world import late_row

    w = build_regime_world(Lake(tmp_path / "lake", registry=DEFAULT_REGISTRY.copy()))
    rows = pd.concat(
        [
            late_row("T10Y2Y", d, 9.0, date(2019, 5, 15))
            for d in (date(2019, 1, 15), date(2019, 2, 15))
        ]
    )
    w.lake.write_table("macro_observations", rows, mode="upsert")
    hist = {
        s.as_of: s for s in regime_history(w.lake, date(2019, 5, 1), date(2019, 5, 31), w.config)
    }
    assert regime_at(DataView(w.lake, date(2019, 5, 20), w.config)) == hist[date(2019, 5, 20)]


def test_fred_gaps_are_filled_for_a_few_sessions_only(tmp_path: Path) -> None:
    w = build_regime_world(Lake(tmp_path / "lake", registry=DEFAULT_REGISTRY.copy()))
    w.lake.delete_rows(
        "macro_observations",
        [("series_id", "=", "VIXCLS"), ("obs_date", ">=", date(2019, 5, 1)),
         ("obs_date", "<=", date(2019, 5, 31))],
    )  # fmt: skip
    near = regime_at(DataView(w.lake, date(2019, 5, 6), w.config))  # 3 sessions since 04-30
    far = regime_at(DataView(w.lake, date(2019, 5, 20), w.config))  # 13 sessions
    assert near.vix is not None and "vix" not in near.missing
    assert far.vix is None and "vix" in far.missing
