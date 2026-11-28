"""Stage 31: the news analyst's output contract. `NewsReport` is an `AnalystReport` with
`catalysts`: each a `NewsCatalyst` (an optional date, a description, the one evidence id it
rests on and its expected impact on the price)."""

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from atlas.agents.evidence import cap_confidence
from atlas.core import types
from atlas.core.types.research import (
    AnalystReport,
    Direction,
    Impact,
    NewsCatalyst,
    NewsReport,
    Signal,
)


def _catalyst(**kw: Any) -> NewsCatalyst:
    base: dict[str, Any] = {
        "date": date(2024, 3, 7),
        "description": "Earnings after the close.",
        "evidence_id": "evt:AAA:earnings:2024-03-07",
        "expected_impact": Impact.MIXED,
    }
    return NewsCatalyst(**{**base, **kw})


def _report(**kw: Any) -> NewsReport:
    base: dict[str, Any] = {
        "ticker": "AAA",
        "as_of": date(2024, 3, 1),
        "analyst": "news_analyst",
        "summary": "Catalysts: earnings on 2024-03-07 [E:evt:AAA:earnings:2024-03-07].",
        "signals": (Signal(name="news_flow", direction=Direction.FLAT, strength=0.2),),
        "risks": ("A miss would hit the stock.",),
        "confidence": 0.5,
        "catalysts": (_catalyst(),),
    }
    return NewsReport(**{**base, **kw})


def test_impact_values() -> None:
    assert [i.value for i in Impact] == ["POSITIVE", "NEGATIVE", "MIXED"]
    assert Impact("MIXED") is Impact.MIXED


def test_a_catalyst_has_an_optional_date_a_description_an_evidence_id_and_an_impact() -> None:
    c = _catalyst()
    assert (c.date, c.evidence_id, c.expected_impact) == (
        date(2024, 3, 7),
        "evt:AAA:earnings:2024-03-07",
        Impact.MIXED,
    )
    assert _catalyst(date=None).date is None
    undated = NewsCatalyst.model_validate(
        {"description": "x", "evidence_id": "news:1", "expected_impact": "POSITIVE"}
    )
    assert undated.date is None


@pytest.mark.parametrize("missing", ["description", "evidence_id", "expected_impact"])
def test_every_field_but_the_date_is_required(missing: str) -> None:
    data = _catalyst().model_dump()
    del data[missing]
    with pytest.raises(ValidationError, match=missing):
        NewsCatalyst.model_validate(data)


@pytest.mark.parametrize("field", ["description", "evidence_id"])
@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_text_is_refused(field: str, bad: str) -> None:
    with pytest.raises(ValidationError, match=field):
        _catalyst(**{field: bad})


@pytest.mark.parametrize("bad", ["up", "", None, True])
def test_an_unknown_impact_is_refused(bad: object) -> None:
    with pytest.raises(ValidationError, match="expected_impact"):
        _catalyst(expected_impact=bad)


def test_unknown_keys_are_refused() -> None:
    with pytest.raises(ValidationError, match="extra"):
        _catalyst(probability=0.4)


def test_a_news_report_is_an_analyst_report_with_catalysts() -> None:
    r = _report()
    assert isinstance(r, AnalystReport)
    assert r.catalysts == (_catalyst(),)
    assert _report(catalysts=()).catalysts == ()
    data = _report().model_dump()
    del data["catalysts"]
    assert NewsReport.model_validate(data).catalysts == ()


def test_a_news_report_round_trips_through_json() -> None:
    r = _report()
    data = r.model_dump(mode="json")
    assert data["catalysts"][0]["date"] == "2024-03-07"
    assert data["catalysts"][0]["expected_impact"] == "MIXED"
    assert NewsReport.model_validate_json(r.model_dump_json()) == r


def test_a_news_report_does_not_validate_as_a_plain_analyst_report() -> None:
    """The catalysts are not silently dropped: an `AnalystReport` refuses them."""
    with pytest.raises(ValidationError, match="extra"):
        AnalystReport.model_validate_json(_report().model_dump_json())


def test_the_confidence_cap_keeps_the_subclass() -> None:
    capped = cap_confidence(_report(confidence=0.9), 0.2)
    assert isinstance(capped, NewsReport) and capped.confidence == 0.2
    assert capped.catalysts == _report().catalysts


def test_the_core_types_package_exports_them() -> None:
    for name in ("Impact", "NewsCatalyst", "NewsReport"):
        assert name in types.__all__
        assert getattr(types, name) is globals()[name]
