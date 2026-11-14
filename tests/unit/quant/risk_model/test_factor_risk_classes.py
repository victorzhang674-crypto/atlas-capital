"""Phase 2 checkpoint (backlog, Stage 19): a company listed under several share classes
(GOOGL / GOOG, FOX / FOXA, NWS / NWSA, UA / UAA) carries the whole company's market cap on
each listing; in the cap-weighted sums and the regression weights it must count once.

`CrossSection.classes` / `build_risk_model(classes=)`: the number of listed classes of each
name's company in the cross-section (same SEC CIK). Each listing then carries `cap / n` in the
cap-weighted sums (sector constraint, style centring) and `sqrt(cap) / n` as its regression
weight, so the listings together weigh what one listing of the company would."""

import dataclasses
from datetime import date

import numpy as np
import pandas as pd
import pytest
from factor_risk_world import cross_sections, make_world
from factor_world import AS_OF, STOCKS, build_factor_world

import atlas.quant.risk_model.factor_model as fm_module
from atlas.core.config import AtlasConfig
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.data.view import DataView
from atlas.quant.risk_model.factor_model import FactorSet, build_risk_model, fit_factor_history
from atlas.quant.risk_model.loader import listed_classes, load_cross_sections, style_inputs
from atlas.quant.risk_model.regression import sector_constraint

SMALL = AtlasConfig.model_validate(
    {
        "quant": {
            "factors": {"min_names": 5},
            "risk_model": {"factor_model": {"min_cross_section": 20}},
        }
    }
)
FM = SMALL.quant.risk_model.factor_model
FACTORS = FactorSet.from_config(FM)


def _duplicated(n: int = 120, t: int = 3) -> tuple[list, str, str]:
    """A world's cross-sections with its first name listed a second time (same returns,
    styles, sector and the whole company's cap)."""
    w = make_world(n=n, t=t, seed=7)
    first = w.symbols[0]
    twin = f"{first}X"
    out = []
    for cs in cross_sections(w):

        def dup(s: pd.Series) -> pd.Series:
            return pd.concat([s, pd.Series([s[first]], index=[twin])])

        out.append(
            dataclasses.replace(
                cs,
                returns=dup(cs.returns),
                raw_styles=pd.concat(
                    [cs.raw_styles, cs.raw_styles.loc[[first]].rename({first: twin})]
                ),
                sectors=dup(cs.sectors),
                caps=dup(cs.caps),
                classes=pd.Series(1, index=[*cs.caps.index, twin]).where(
                    ~pd.Index([*cs.caps.index, twin]).isin([first, twin]), 2
                ),
            )
        )
    return out, first, twin


def test_regression_weights_and_constraint_count_a_company_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[pd.Series, pd.Series, pd.DataFrame, pd.Series]] = []
    real = fm_module.constrained_wls

    def spy(returns, exposures, weights, constraint):  # type: ignore[no-untyped-def]
        seen.append((weights, constraint, exposures, returns))
        return real(returns, exposures, weights, constraint)

    monkeypatch.setattr(fm_module, "constrained_wls", spy)
    sections, first, twin = _duplicated()
    fit_factor_history(sections, FACTORS, FM)
    weights, constraint, _, _ = seen[0]
    cap = float(sections[0].caps[first])
    assert weights[first] + weights[twin] == pytest.approx(np.sqrt(cap))
    design = seen[0][2].drop(index=twin)
    single = sections[0].caps.loc[design.index]
    want = sector_constraint(design, single, FACTORS.sectors)
    pd.testing.assert_series_equal(constraint, want)


def test_without_classes_nothing_changes() -> None:
    """`classes=None` (the default) is one listing per name: the fit is unchanged."""
    w = make_world(n=120, t=3, seed=7)
    plain = fit_factor_history(cross_sections(w), FACTORS, FM)
    ones = [
        dataclasses.replace(cs, classes=pd.Series(1, index=cs.caps.index))
        for cs in cross_sections(w)
    ]
    again = fit_factor_history(ones, FACTORS, FM)
    pd.testing.assert_frame_equal(plain.factor_returns, again.factor_returns)


def test_build_risk_model_divides_the_caps_by_the_classes() -> None:
    w = make_world(n=150, t=200, seed=3)
    hist = fit_factor_history(cross_sections(w), FACTORS, FM)
    day = w.dates[-1].date()
    classes = pd.Series(1, index=w.caps.index)
    classes.iloc[:2] = 2
    got = build_risk_model(hist, w.styles, w.sectors, w.caps, day, SMALL, classes=classes)
    want = build_risk_model(hist, w.styles, w.sectors, w.caps / classes, day, SMALL)
    np.testing.assert_array_equal(got.x, want.x)
    assert got.symbols == want.symbols


# --- the loader: classes from the issuers' CIKs ------------------------------------------------


@pytest.fixture(scope="module")
def twin_lake(tmp_path_factory: pytest.TempPathFactory) -> Lake:
    """The factor world with BBB filing under AAA's CIK (two classes of one company)."""
    lake = Lake(tmp_path_factory.mktemp("twin") / "lake", registry=DEFAULT_REGISTRY.copy())
    build_factor_world(lake)
    facts = lake.read_table("fundamentals_facts")
    aaa_cik = int(facts.loc[facts["symbol"] == "AAA", "cik"].iloc[0])
    lake.write_table(
        "fundamentals_facts",
        facts[facts["symbol"] == "BBB"].assign(cik=aaa_cik),
        mode="upsert",
    )
    return lake


def test_view_ciks(twin_lake: Lake) -> None:
    view = DataView(twin_lake, AS_OF, SMALL)
    ciks = view.ciks(["AAA", "BBB", "CCC", "ZZZ"])
    assert ciks["AAA"] == ciks["BBB"] and ciks["CCC"] != ciks["AAA"]
    assert "ZZZ" not in ciks.index  # no facts: no CIK
    early = DataView(twin_lake, date(2019, 1, 2), SMALL)  # no fact known yet
    assert early.ciks(["AAA"]).empty


def test_listed_classes_and_style_inputs(twin_lake: Lake) -> None:
    view = DataView(twin_lake, AS_OF, SMALL)
    classes = listed_classes(view, list(STOCKS))
    assert classes["AAA"] == classes["BBB"] == 2
    assert (classes.drop(["AAA", "BBB"]) == 1).all()
    inputs = style_inputs(view)
    assert inputs.classes["AAA"] == 2 and inputs.classes["CCC"] == 1
    sections = load_cross_sections(twin_lake, date(2024, 2, 26), date(2024, 2, 29), SMALL)
    assert all(cs.classes is not None and cs.classes["BBB"] == 2 for cs in sections)
