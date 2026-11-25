"""Stage 30: the technical analyst's output contract. `TechnicalReport` is an `AnalystReport`
whose signals are `TechnicalSignal`s, each with a `timing` and a `suggested_stop_pct` (a
fraction of the last close)."""

import math
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from atlas.agents.evidence import cap_confidence, check_citations
from atlas.core import types
from atlas.core.types.research import (
    AnalystReport,
    Direction,
    Signal,
    TechnicalReport,
    TechnicalSignal,
    Timing,
)


def _signal(**kw: Any) -> TechnicalSignal:
    base: dict[str, Any] = {
        "name": "trend",
        "direction": Direction.LONG,
        "strength": 0.6,
        "evidence_ids": ("pxs:AAA:2024-02-29:ma",),
        "timing": Timing.WAIT_FOR_PULLBACK,
        "suggested_stop_pct": 0.08,
    }
    return TechnicalSignal(**{**base, **kw})


def _report(**kw: Any) -> TechnicalReport:
    base: dict[str, Any] = {
        "ticker": "AAA",
        "as_of": date(2024, 3, 1),
        "analyst": "technical_analyst",
        "summary": "Trend: up [E:pxs:AAA:2024-02-29:ma].",
        "signals": (_signal(),),
        "risks": ("A close below the 50-day average breaks the trend.",),
        "confidence": 0.9,
    }
    return TechnicalReport(**{**base, **kw})


def test_timing_values() -> None:
    assert [t.value for t in Timing] == ["NOW", "WAIT_FOR_PULLBACK", "AVOID"]
    assert Timing("AVOID") is Timing.AVOID


def test_a_technical_signal_is_a_signal_with_timing_and_stop() -> None:
    s = _signal()
    assert isinstance(s, Signal)
    assert (s.timing, s.suggested_stop_pct) == (Timing.WAIT_FOR_PULLBACK, 0.08)
    assert _signal(suggested_stop_pct=None, direction=Direction.FLAT).suggested_stop_pct is None
    assert _signal(timing="NOW").timing is Timing.NOW


def test_the_timing_is_required() -> None:
    with pytest.raises(ValidationError, match="timing"):
        TechnicalSignal(name="trend", direction=Direction.LONG, strength=0.5)


@pytest.mark.parametrize("bad", [0.0, 1.0, -0.05, 8.0, math.nan, math.inf, True])
def test_the_stop_is_a_fraction_strictly_between_0_and_1(bad: object) -> None:
    with pytest.raises(ValidationError, match="suggested_stop_pct"):
        _signal(suggested_stop_pct=bad)


@pytest.mark.parametrize("bad", ["now", "WAIT", "", None])
def test_an_unknown_timing_is_refused(bad: object) -> None:
    with pytest.raises(ValidationError, match="timing"):
        _signal(timing=bad)


def test_unknown_keys_are_refused() -> None:
    with pytest.raises(ValidationError, match="extra"):
        _signal(entry_price=100.0)


def test_a_technical_report_is_an_analyst_report_of_technical_signals() -> None:
    r = _report()
    assert isinstance(r, AnalystReport)
    assert isinstance(r.signals[0], TechnicalSignal)
    plain = Signal(name="trend", direction=Direction.LONG, strength=0.5)
    with pytest.raises(ValidationError, match="instance of TechnicalSignal"):
        _report(signals=(plain,))
    with pytest.raises(ValidationError, match="timing"):
        _report(signals=(plain.model_dump(),))


def test_a_technical_report_round_trips_through_json() -> None:
    r = _report()
    data = r.model_dump(mode="json")
    assert data["signals"][0]["timing"] == "WAIT_FOR_PULLBACK"
    assert data["signals"][0]["suggested_stop_pct"] == 0.08
    assert TechnicalReport.model_validate_json(r.model_dump_json()) == r


def test_a_technical_report_does_not_validate_as_a_plain_analyst_report() -> None:
    """The extra fields are not silently dropped: an `AnalystReport` refuses them."""
    with pytest.raises(ValidationError, match="extra"):
        AnalystReport.model_validate_json(_report().model_dump_json())


def test_the_confidence_cap_and_the_citation_check_keep_the_subclass() -> None:
    capped = cap_confidence(_report(), 0.4)
    assert isinstance(capped, TechnicalReport) and capped.confidence == 0.4
    assert capped.signals == _report().signals
    citations = check_citations(_report(), {"pxs:AAA:2024-02-29:ma"})
    assert citations.ok and citations.cited == ("pxs:AAA:2024-02-29:ma",)


def test_the_core_types_package_exports_them() -> None:
    for name in ("Timing", "TechnicalSignal", "TechnicalReport"):
        assert name in types.__all__
        assert getattr(types, name) is globals()[name]
