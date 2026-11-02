"""Tests for research contracts: signals, evidence, reports, debate and theses."""

from datetime import UTC, date, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from atlas.core.types.ids import IdPrefix, new_id
from atlas.core.types.research import (
    AnalystReport,
    AssetView,
    Catalyst,
    Claim,
    DebateRole,
    DebateTurn,
    Direction,
    Evidence,
    InvestmentThesis,
    MacroOutlook,
    ReturnDistribution,
    Signal,
    ThesisStatus,
)

AS_OF = date(2024, 1, 2)


def _signal(**kw: Any) -> Signal:
    return Signal(**{"name": "valuation", "direction": Direction.LONG, "strength": 0.6, **kw})


def _report(**kw: Any) -> AnalystReport:
    base: dict[str, Any] = {
        "ticker": "aapl",
        "as_of": AS_OF,
        "analyst": "fundamental",
        "summary": "Cheap on FCF yield.",
        "signals": (_signal(),),
        "risks": ("China demand",),
        "confidence": 0.7,
    }
    return AnalystReport(**{**base, **kw})


def _macro(**kw: Any) -> MacroOutlook:
    base: dict[str, Any] = {
        "as_of": AS_OF,
        "regime_summary": "Late cycle",
        "growth": "slowing",
        "inflation": "sticky",
        "policy": "on hold",
        "credit": "tight spreads",
        "risks": (),
        "asset_views": (),
        "sector_tilts": {"Energy": 0.5},
        "risk_appetite": 0.4,
        "evidence": (),
    }
    return MacroOutlook(**{**base, **kw})


def _claim(**kw: Any) -> Claim:
    return Claim(**{"text": "Margins expand", "evidence_ids": ("news:1",), "strength": 0.5, **kw})


def _thesis(**kw: Any) -> InvestmentThesis:
    base: dict[str, Any] = {
        "id": new_id(IdPrefix.THESIS),
        "ticker": "msft",
        "as_of": AS_OF,
        "direction": Direction.LONG,
        "exp_return": ReturnDistribution(p10=-0.05, p50=0.04, p90=0.12),
        "horizon_days": 63,
        "conviction": 0.65,
        "kill_criteria": ("price_below(300)",),
        "evidence": ("news:1",),
    }
    return InvestmentThesis(**{**base, **kw})


def test_return_distribution_ordered() -> None:
    ReturnDistribution(p10=0.01, p50=0.01, p90=0.01)
    ReturnDistribution(p10=-1.0, p50=0.0, p90=2.0)
    for p10, p50, p90 in [(0.02, 0.01, 0.03), (0.0, 0.05, 0.04), (-1.5, 0.0, 0.1)]:
        with pytest.raises(ValidationError):
            ReturnDistribution(p10=p10, p50=p50, p90=p90)
    with pytest.raises(ValidationError):
        ReturnDistribution(p10=float("nan"), p50=0.0, p90=0.1)


_BUILDERS = {
    "report.confidence": lambda v: _report(confidence=v),
    "signal.strength": lambda v: _signal(strength=v),
    "thesis.conviction": lambda v: _thesis(conviction=v),
    "macro.risk_appetite": lambda v: _macro(risk_appetite=v),
    "claim.strength": lambda v: _claim(strength=v),
    "asset_view.conviction": lambda v: AssetView(
        etf="TLT", direction=Direction.LONG, conviction=v, horizon_days=20
    ),
}


@pytest.mark.parametrize("field", sorted(_BUILDERS))
@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan")])
def test_confidence_outside_unit_interval_rejected(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        _BUILDERS[field](value)


@pytest.mark.parametrize("field", sorted(_BUILDERS))
def test_confidence_inside_unit_interval_accepted(field: str) -> None:
    _BUILDERS[field](0.0)
    _BUILDERS[field](1.0)


def test_sector_tilt_bounds() -> None:
    assert _macro(sector_tilts={"Tech": -1.0, "Energy": 1.0}).sector_tilts["Tech"] == -1.0
    with pytest.raises(ValidationError):
        _macro(sector_tilts={"Tech": 1.5})
    with pytest.raises(ValidationError):
        _macro(sector_tilts={"Tech": float("nan")})


def test_report_normalises_ticker_and_requires_text() -> None:
    assert _report().ticker == "AAPL"
    with pytest.raises(ValidationError):
        _report(summary="  ")
    with pytest.raises(ValidationError):
        _report(analyst="")


def test_evidence_as_of_must_be_aware() -> None:
    ev = Evidence(
        id="news:abc",
        source="alpaca_news",
        as_of=datetime(2024, 1, 2, 14, 30, tzinfo=UTC),
        text="Apple beats",
    )
    assert ev.url is None
    with pytest.raises(ValidationError):
        Evidence(id="news:abc", source="x", as_of=datetime(2024, 1, 2, 14, 30), text="t")
    with pytest.raises(ValidationError):
        Evidence(id=" ", source="x", as_of=datetime(2024, 1, 2, tzinfo=UTC), text="t")


def test_debate_turn() -> None:
    turn = DebateTurn(speaker=DebateRole.RED_TEAM, claims=(_claim(),))
    assert turn.concessions == ()
    assert turn.questions_for_other_side == ()
    assert [r.value for r in DebateRole] == ["BULL", "BEAR", "RED_TEAM"]


def test_thesis_defaults_active() -> None:
    thesis = _thesis(catalysts=(Catalyst(description="Q2 earnings", expected_date=AS_OF),))
    assert thesis.status is ThesisStatus.ACTIVE
    assert thesis.ticker == "MSFT"
    assert [s.value for s in ThesisStatus] == ["ACTIVE", "INVALIDATED", "CLOSED", "EXPIRED"]


def test_thesis_horizon_positive() -> None:
    with pytest.raises(ValidationError):
        _thesis(horizon_days=0)


def test_thesis_rejects_wrong_id_prefix() -> None:
    with pytest.raises(ValidationError):
        _thesis(id=new_id(IdPrefix.ORDER))


def test_direction_sign() -> None:
    assert (Direction.LONG.sign, Direction.SHORT.sign, Direction.FLAT.sign) == (1, -1, 0)


# --- Phase 0 checkpoint, Tier 2: text items and map keys ------------------------------------

_EMPTY_ITEM_CASES: dict[str, Any] = {
    "signal.evidence_ids": lambda: _signal(evidence_ids=("e:1", "")),
    "report.risks": lambda: _report(risks=("  ",)),
    "macro.risks": lambda: _macro(risks=("",)),
    "macro.evidence": lambda: _macro(evidence=("",)),
    "claim.evidence_ids": lambda: _claim(evidence_ids=("",)),
    "turn.concessions": lambda: DebateTurn(speaker=DebateRole.BULL, claims=(), concessions=("",)),
    "turn.questions": lambda: DebateTurn(
        speaker=DebateRole.BEAR, claims=(), questions_for_other_side=(" ",)
    ),
    "thesis.kill_criteria": lambda: _thesis(kill_criteria=("",)),
    "thesis.evidence": lambda: _thesis(evidence=("",)),
}


@pytest.mark.parametrize("case", sorted(_EMPTY_ITEM_CASES))
def test_empty_text_items_rejected(case: str) -> None:
    with pytest.raises(ValidationError, match="at least 1 character"):
        _EMPTY_ITEM_CASES[case]()


def test_text_items_are_stripped() -> None:
    assert _thesis(evidence=(" news:1 ",)).evidence == ("news:1",)
    assert _report(risks=("China demand\n",)).risks == ("China demand",)


def test_sector_tilt_keys_non_empty_and_unique() -> None:
    assert _macro(sector_tilts={" Energy ": 0.5}).sector_tilts == {"Energy": 0.5}
    with pytest.raises(ValidationError, match="at least 1 character"):
        _macro(sector_tilts={" ": 0.1})
    for bad in ({"Energy": 0.1, "energy": -0.1}, {"Energy": 0.1, "Energy ": 0.2}):
        with pytest.raises(ValidationError, match="same entry"):
            _macro(sector_tilts=bad)
