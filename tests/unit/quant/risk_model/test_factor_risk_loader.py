"""The DataView -> cross-section loader (Stage 19 Task 9), on the Stage 16 synthetic lake."""

import ast
import dataclasses
import inspect
from datetime import date

import numpy as np
import pandas as pd
import pytest
from factor_world import AS_OF, STOCKS, build_factor_world

import atlas.quant.risk_model.loader as loader_module
from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.quant.risk_model.factor_model import STYLE_NAMES, CrossSection
from atlas.quant.risk_model.loader import load_cross_sections, style_inputs

CFG = AtlasConfig.model_validate({"quant": {"factors": {"min_names": 5}}})
START, END = date(2024, 1, 29), date(2024, 2, 29)


@pytest.fixture(scope="module")
def world_lake(tmp_path_factory: pytest.TempPathFactory) -> Lake:
    from atlas.core.storage import DEFAULT_REGISTRY

    lake = Lake(tmp_path_factory.mktemp("lake") / "lake", registry=DEFAULT_REGISTRY.copy())
    build_factor_world(lake)
    return lake


@pytest.fixture(scope="module")
def sections(world_lake: Lake) -> list[CrossSection]:
    return load_cross_sections(world_lake, START, END, CFG)


def test_style_inputs_shapes_and_timing(world_lake: Lake) -> None:
    inputs = style_inputs(DataView(world_lake, AS_OF, CFG))
    assert inputs.data_date == date(2024, 2, 29)  # the morning of 2024-03-01 knows the 29th's bar
    assert list(inputs.raw_styles.columns) == list(STYLE_NAMES)
    assert set(inputs.raw_styles.index) <= set(STOCKS) and len(inputs.raw_styles) >= 11
    assert inputs.sectors.index.equals(inputs.raw_styles.index)
    assert (inputs.caps.dropna() > 0).all()
    np.testing.assert_allclose(inputs.raw_styles["size"], np.log(inputs.caps), rtol=1e-12)
    assert inputs.raw_styles[["value", "momentum", "liquidity"]].notna().any().all()
    # no SPY bars in this world: beta is unavailable and stays missing (never guessed)
    assert inputs.raw_styles["beta"].isna().all()


def test_cross_sections_one_per_session(sections: list[CrossSection]) -> None:
    days = [cs.day for cs in sections]
    assert days[0] == START and days[-1] == END and days == sorted(set(days))
    assert len(days) == 23  # Presidents Day is not a session
    cs = sections[5]
    assert isinstance(cs, CrossSection)
    assert list(cs.raw_styles.columns) == list(STYLE_NAMES)
    assert cs.returns.index.equals(cs.raw_styles.index) and cs.caps.index.equals(
        cs.raw_styles.index
    )
    assert cs.sectors.index.equals(cs.raw_styles.index)
    assert cs.returns.abs().max() < 0.2 and cs.returns.notna().all()


def _backing_bytes(values: np.ndarray) -> int:
    """Bytes of the buffer that backs `values`: its own, or the array it is a view of."""
    root = values
    while isinstance(root.base, np.ndarray):
        root = root.base
    return int(root.nbytes)


def test_cross_sections_own_their_numbers(sections: list[CrossSection]) -> None:
    # A row view into a (sessions x names) slice of the price panel keeps that whole slice
    # alive in every cross-section: memory grows with sessions^2 x names, and the ten-year
    # main-lake build was killed by the system for it (2026-11-12).
    for cs in sections:
        names = len(cs.raw_styles)
        own = 8 * names * len(STYLE_NAMES)  # the cross-section's own float64 block
        for style in STYLE_NAMES:
            assert _backing_bytes(cs.raw_styles[style].to_numpy()) <= own, (cs.day, style)
        assert _backing_bytes(cs.returns.to_numpy()) <= own, (cs.day, "returns")
        assert _backing_bytes(cs.caps.to_numpy()) <= own, (cs.day, "caps")


def test_size_follows_the_previous_close(sections: list[CrossSection]) -> None:
    for cs in sections[:6]:
        np.testing.assert_allclose(cs.raw_styles["size"], np.log(cs.caps), rtol=1e-12)
    # caps move with the adjusted close: day t's cap / day t-1's cap = 1 + return of day t-1
    a, b, c = sections[3], sections[4], sections[5]
    ratio = b.caps / a.caps
    np.testing.assert_allclose(ratio, 1.0 + a.returns, rtol=1e-9)
    np.testing.assert_allclose(c.caps / b.caps, 1.0 + b.returns, rtol=1e-9)


def test_exposures_never_see_the_session_being_regressed(
    world_lake: Lake, sections: list[CrossSection], tmp_path
) -> None:
    """Rewriting the bars of the last session changes that session's returns only: every
    exposure input of every day, including the last one, is unchanged."""
    from atlas.core.storage import DEFAULT_REGISTRY

    lake2 = Lake(tmp_path / "lake2", registry=DEFAULT_REGISTRY.copy())
    build_factor_world(lake2)
    bars = lake2.read_table("prices_daily")
    hit = bars["date"] == END
    for col in ("open", "high", "low", "close", "adj_close"):
        bars.loc[hit, col] = bars.loc[hit, col] * 1.04
    lake2.write_table("prices_daily", bars, mode="upsert")
    other = load_cross_sections(lake2, START, END, CFG)
    assert len(other) == len(sections)
    for before, after in zip(sections, other, strict=True):
        pd.testing.assert_frame_equal(before.raw_styles, after.raw_styles)
        pd.testing.assert_series_equal(before.caps, after.caps)
        if before.day < END:
            pd.testing.assert_series_equal(before.returns, after.returns)
    assert not np.allclose(sections[-1].returns, other[-1].returns)


def test_window_without_sessions_raises(world_lake: Lake) -> None:
    with pytest.raises(DataError, match="no trading session"):
        load_cross_sections(world_lake, date(2024, 2, 24), date(2024, 2, 25), CFG)


def test_loader_uses_only_dataview() -> None:
    tree = ast.parse(inspect.getsource(loader_module))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not any(m.startswith("atlas.data.") and m != "atlas.data.view" for m in imported)
    calls = {
        n.func.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert not calls & {"read_table", "read_rows", "now", "utcnow", "today"}


def test_anchor_months_holds_fundamental_styles_longer(world_lake: Lake) -> None:
    quarterly = AtlasConfig.model_validate(
        {
            "quant": {
                "factors": {"min_names": 5},
                "risk_model": {"factor_model": {"anchor_months": 3}},
            }
        }
    )
    xs = {c.day: c for c in load_cross_sections(world_lake, START, END, quarterly)}
    fundamental = ["value", "quality", "growth", "leverage"]
    # one December anchor serves January and February
    pd.testing.assert_frame_equal(
        xs[date(2024, 1, 29)].raw_styles[fundamental], xs[END].raw_styles[fundamental]
    )
    monthly = {c.day: c for c in load_cross_sections(world_lake, START, END, CFG)}
    assert (
        not monthly[date(2024, 1, 29)]
        .raw_styles[fundamental]
        .equals(monthly[END].raw_styles[fundamental])
    )


# --- audit follow-up: wiring of the real-data entry points --------------------------------------

from factor_risk_world import cross_sections, make_world  # noqa: E402

import atlas.quant.risk_model.loader as loader  # noqa: E402
from atlas.core.calendar import TradingCalendar  # noqa: E402
from atlas.quant.risk_model.factor_model import FactorHistory, FactorSet, RiskModel  # noqa: E402
from atlas.quant.risk_model.loader import (  # noqa: E402
    StyleInputs,
    build_and_store,
    build_factor_history,
    build_model_from_view,
    etf_exposures_from_view,
)
from atlas.quant.risk_model.store import RiskModelStore  # noqa: E402

SMALL = AtlasConfig.model_validate(
    {
        "quant": {
            "factors": {"min_names": 5},
            "risk_model": {"factor_model": {"min_cross_section": 50}},
        }
    }
)


def _random_history(days: list[date], seed: int = 0) -> FactorHistory:
    factors = FactorSet.from_config(SMALL.quant.risk_model.factor_model)
    idx = pd.DatetimeIndex(pd.to_datetime(days), name="date")
    rng = np.random.default_rng(seed)
    fr = pd.DataFrame(
        rng.normal(scale=0.01, size=(len(idx), len(factors.names))),
        index=idx,
        columns=list(factors.names),
    )
    return FactorHistory(
        factors=factors,
        factor_returns=fr,
        residuals=pd.DataFrame(index=idx),
        r2=pd.Series(0.5, index=idx),
        n_names=pd.Series(100, index=idx),
    )


def test_build_factor_history_fits_the_loaded_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    w = make_world(n=120, t=30, seed=81)
    seen = {}

    def fake_load(lake, start, end, config, *, symbols=None, factor_store=None):
        seen["args"] = (start, end, symbols)
        seen["factor_store"] = factor_store
        return cross_sections(w)

    monkeypatch.setattr(loader, "load_cross_sections", fake_load)
    hist = build_factor_history(None, date(2015, 1, 2), date(2015, 2, 13), SMALL, symbols=["X"])  # type: ignore[arg-type]
    assert seen["args"] == (date(2015, 1, 2), date(2015, 2, 13), ["X"])
    assert seen["factor_store"] is None  # Tier 3 item 4: the cache is opt-in
    assert len(hist.factor_returns) == 30 and hist.skipped == ()


def test_build_model_from_view_uses_the_view_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    w = make_world(n=150, t=120, seed=82)
    fm = SMALL.quant.risk_model.factor_model
    from atlas.quant.risk_model.factor_model import fit_factor_history

    hist = fit_factor_history(cross_sections(w), FactorSet.from_config(fm), fm)
    day = w.dates[100].date()
    monkeypatch.setattr(
        loader,
        "style_inputs",
        lambda view, symbols=None: StyleInputs(day, w.styles, w.sectors, w.caps),
    )

    class FakeView:
        config = SMALL

    model = build_model_from_view(FakeView(), hist)  # type: ignore[arg-type]
    assert isinstance(model, RiskModel) and model.as_of == day and model.n_days == 101


def test_etf_exposures_from_view(world_lake: Lake) -> None:
    view = DataView(world_lake, AS_OF, CFG)
    days = list(TradingCalendar().sessions(date(2022, 6, 1), date(2024, 2, 29)))
    hist = _random_history(days)
    out = etf_exposures_from_view(view, hist, ["AAA", "BBB"])
    assert list(out.betas.index) == ["AAA", "BBB"] and (out.n_obs == 252).all()
    # factor returns after the view's last known session are never used
    later = _random_history([*days, date(2024, 3, 1)], seed=0)
    fr = later.factor_returns.copy()
    fr.iloc[-1] = 5.0
    later = FactorHistory(later.factors, fr, later.residuals, later.r2, later.n_names)
    pd.testing.assert_frame_equal(
        etf_exposures_from_view(view, later, ["AAA", "BBB"]).betas, out.betas
    )
    with pytest.raises(DataError, match="no ETFs"):
        etf_exposures_from_view(view, hist, [])


def test_build_and_store_writes_returns_and_month_end_snapshots(
    monkeypatch: pytest.MonkeyPatch, tmp_lake: Lake
) -> None:
    cal = TradingCalendar()
    days = list(cal.sessions(date(2023, 6, 1), date(2023, 12, 29)))
    hist = _random_history(days)
    monkeypatch.setattr(loader, "build_factor_history", lambda *a, **k: hist)
    built: list[date] = []

    def fake_model(view, upto, symbols=None):
        built.append(view.day)
        assert upto.factor_returns.index[-1] < pd.Timestamp(view.day)  # no look-ahead
        return RiskModel(
            as_of=upto.factor_returns.index[-1].date(),
            symbols=("A",),
            factors=hist.factors.names,
            x=np.ones((1, 20)),
            f=np.eye(20) * 1e-4,
            d=np.ones(1) * 1e-4,
            n_days=len(upto.factor_returns),
        )

    monkeypatch.setattr(loader, "build_model_from_view", fake_model)
    store = RiskModelStore(tmp_lake, SMALL)
    out_hist, dates = build_and_store(tmp_lake, store, date(2023, 6, 1), date(2023, 12, 29), SMALL)
    assert out_hist is hist
    # month-ends with >= 63 sessions of history: from late August on
    assert dates == (
        date(2023, 8, 31),
        date(2023, 9, 29),
        date(2023, 10, 31),
        date(2023, 11, 30),
        date(2023, 12, 29),
    )
    assert built == [cal.next_session(d) for d in dates]
    assert store.snapshot_dates() == dates
    assert store.read_factor_returns(date(2024, 1, 2)) is not None


def test_build_and_store_advances_one_view_for_the_snapshots(
    monkeypatch: pytest.MonkeyPatch, tmp_lake: Lake
) -> None:
    """Phase 2 checkpoint: a fresh `DataView` per month-end re-resolves every symbol's
    fundamentals; one view advanced month by month shares the cache and reads the same data."""
    cal = TradingCalendar()
    days = list(cal.sessions(date(2023, 6, 1), date(2023, 12, 29)))
    hist = _random_history(days)
    monkeypatch.setattr(loader, "build_factor_history", lambda *a, **k: hist)
    opened: list[date] = []
    seen: list[tuple[date, int]] = []
    real_view = loader.DataView

    def counting_view(lake: Lake, day: date, config: AtlasConfig) -> DataView:
        opened.append(day)
        return real_view(lake, day, config)

    def fake_model(view: DataView, upto: FactorHistory, symbols: object = None) -> RiskModel:
        seen.append((view.day, id(view._shared)))
        return RiskModel(
            as_of=upto.factor_returns.index[-1].date(),
            symbols=("A",),
            factors=hist.factors.names,
            x=np.ones((1, 20)),
            f=np.eye(20) * 1e-4,
            d=np.ones(1) * 1e-4,
            n_days=len(upto.factor_returns),
        )

    monkeypatch.setattr(loader, "DataView", counting_view)
    monkeypatch.setattr(loader, "build_model_from_view", fake_model)
    store = RiskModelStore(tmp_lake, SMALL)
    _, dates = build_and_store(tmp_lake, store, date(2023, 6, 1), date(2023, 12, 29), SMALL)
    assert len(dates) == 5
    assert opened == [cal.next_session(dates[0])]  # one view opened, then advanced
    assert [d for d, _ in seen] == [cal.next_session(d) for d in dates]
    assert len({shared for _, shared in seen}) == 1  # every month shares one cache


def test_build_and_store_keeps_a_snapshot_whose_etfs_lack_history(
    monkeypatch: pytest.MonkeyPatch, tmp_lake: Lake
) -> None:
    """Phase 2 checkpoint: early month-ends have enough history for a model (`min_history`)
    but not for ETF betas (`etf.min_obs`). Such a snapshot is stored without ETFs (they are
    then outside the model, never guessed) instead of failing the whole build."""
    cal = TradingCalendar()
    days = list(cal.sessions(date(2023, 6, 1), date(2023, 12, 29)))
    hist = _random_history(days)
    monkeypatch.setattr(loader, "build_factor_history", lambda *a, **k: hist)

    def fake_model(view: DataView, upto: FactorHistory, symbols: object = None) -> RiskModel:
        return RiskModel(
            as_of=upto.factor_returns.index[-1].date(),
            symbols=("A",),
            factors=hist.factors.names,
            x=np.ones((1, 20)),
            f=np.eye(20) * 1e-4,
            d=np.ones(1) * 1e-4,
            n_days=len(upto.factor_returns),
        )

    asked: list[date] = []

    def fake_etfs(view: DataView, upto: FactorHistory, etfs: object = None) -> object:
        asked.append(view.day)
        if len(upto.factor_returns) < 126:
            raise DataError("no ETF has 126 observations")
        return "betas"

    def fake_extend(model: RiskModel, exposures: object) -> RiskModel:
        assert exposures == "betas"
        return dataclasses.replace(model, etfs=("SPY",))

    stored: list[tuple[date, tuple[str, ...]]] = []
    monkeypatch.setattr(loader, "build_model_from_view", fake_model)
    monkeypatch.setattr(loader, "etf_exposures_from_view", fake_etfs)
    monkeypatch.setattr(loader, "extend_model", fake_extend)
    store = RiskModelStore(tmp_lake, SMALL)
    monkeypatch.setattr(
        store, "write_snapshot", lambda model: stored.append((model.as_of, model.etfs)) or 1
    )
    _, dates = build_and_store(
        tmp_lake, store, date(2023, 6, 1), date(2023, 12, 29), SMALL, etfs=["SPY"]
    )
    assert len(dates) == 5 and len(asked) == 5
    # 2023-08-31 (64 sessions), 09-29 (84) and 10-31 (106) are short of 126; 11-30 has 127
    assert stored == [
        (date(2023, 8, 31), ()),
        (date(2023, 9, 29), ()),
        (date(2023, 10, 31), ()),
        (date(2023, 11, 30), ("SPY",)),
        (date(2023, 12, 29), ("SPY",)),
    ]


# --- Phase 2 checkpoint, Tier 3 item 4: monthly anchors read from the factor cache --------------

ANCHORS = (date(2023, 12, 29), date(2024, 1, 31), date(2024, 2, 29))
"""The month-ends whose fundamental inputs the sessions of START..END use."""


def _same_sections(got: list[CrossSection], want: list[CrossSection]) -> None:
    assert [cs.day for cs in got] == [cs.day for cs in want]
    for a, b in zip(got, want, strict=True):
        pd.testing.assert_frame_equal(a.raw_styles, b.raw_styles, check_exact=False, rtol=1e-12)
        pd.testing.assert_series_equal(a.caps, b.caps, check_exact=False, rtol=1e-12)
        pd.testing.assert_series_equal(a.returns, b.returns)
        pd.testing.assert_series_equal(a.sectors, b.sectors)


def test_anchors_read_from_the_factor_cache_give_the_same_sections(
    world_lake: Lake,
    sections: list[CrossSection],
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from atlas.core.storage import DEFAULT_REGISTRY
    from atlas.quant.factors import FactorStore, build_factor_cache

    scratch = Lake(tmp_path_factory.mktemp("cache") / "lake", registry=DEFAULT_REGISTRY.copy())
    store = FactorStore(scratch, CFG)
    assert build_factor_cache(world_lake, ANCHORS, config=CFG, store=store) == ANCHORS
    monkeypatch.setattr(
        loader_module, "factor_panel", lambda *a, **k: pytest.fail("the cache was not used")
    )
    cached = load_cross_sections(world_lake, START, END, CFG, factor_store=store)
    _same_sections(cached, sections)


def test_an_anchor_missing_from_the_cache_is_computed(
    world_lake: Lake,
    sections: list[CrossSection],
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from atlas.core.storage import DEFAULT_REGISTRY
    from atlas.quant.factors import FactorStore, build_factor_cache

    scratch = Lake(tmp_path_factory.mktemp("cache") / "lake", registry=DEFAULT_REGISTRY.copy())
    store = FactorStore(scratch, CFG)
    build_factor_cache(world_lake, ANCHORS[:2], config=CFG, store=store)  # no February panel
    computed: list[date] = []
    fresh = loader_module.factor_panel

    def counting(view: DataView, symbols: object = None) -> object:
        computed.append(view.day)
        return fresh(view, symbols)  # type: ignore[arg-type]

    monkeypatch.setattr(loader_module, "factor_panel", counting)
    cached = load_cross_sections(world_lake, START, END, CFG, factor_store=store)
    assert computed == [ANCHORS[2]]
    _same_sections(cached, sections)


def test_the_cache_is_not_used_for_a_custom_symbol_list(
    world_lake: Lake, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from atlas.core.storage import DEFAULT_REGISTRY
    from atlas.quant.factors import FactorStore, build_factor_cache

    scratch = Lake(tmp_path_factory.mktemp("cache") / "lake", registry=DEFAULT_REGISTRY.copy())
    store = FactorStore(scratch, CFG)
    build_factor_cache(world_lake, ANCHORS, config=CFG, store=store)
    computed: list[date] = []
    fresh = loader_module.factor_panel

    def counting(view: DataView, symbols: object = None) -> object:
        computed.append(view.day)
        return fresh(view, symbols)  # type: ignore[arg-type]

    monkeypatch.setattr(loader_module, "factor_panel", counting)
    # a stored panel is cleaned over the whole universe: it does not describe a subset
    load_cross_sections(world_lake, START, END, CFG, symbols=list(STOCKS[:9]), factor_store=store)
    assert computed == list(ANCHORS)
