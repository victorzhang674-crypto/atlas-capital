"""Tests for predictions, lessons, NAV records and ledger entries."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from atlas.core.types.ids import IdPrefix, new_id
from atlas.core.types.performance import (
    EntrySide,
    Forecast,
    LedgerEntry,
    Lesson,
    NAVRecord,
    Prediction,
)
from atlas.core.types.research import Direction, ReturnDistribution

MADE = datetime(2024, 1, 2, 21, 0, tzinfo=UTC)


def _forecast() -> Forecast:
    return Forecast(
        direction=Direction.LONG,
        confidence=0.6,
        returns=ReturnDistribution(p10=-0.04, p50=0.02, p90=0.07),
    )


def _prediction(**kw: Any) -> Prediction:
    base: dict[str, Any] = {
        "id": new_id(IdPrefix.PREDICTION),
        "source_agent": "research_director",
        "made_at": MADE,
        "target": "aapl",
        "horizon_days": 21,
        "forecast": _forecast(),
    }
    return Prediction(**{**base, **kw})


def test_prediction_settlement_rules() -> None:
    open_ = _prediction()
    assert open_.target == "AAPL"
    assert not open_.is_settled
    later = MADE + timedelta(days=30)
    settled = _prediction(settled_at=later, outcome=0.031, score=0.16)
    assert settled.is_settled
    assert _prediction(settled_at=later, outcome=-0.02).score is None
    for bad in ({"settled_at": later}, {"outcome": 0.01}, {"score": 0.1}):
        with pytest.raises(ValidationError, match="settle"):
            _prediction(**bad)
    with pytest.raises(ValidationError):
        _prediction(settled_at=later, outcome=float("nan"))


def test_settled_before_made_rejected() -> None:
    with pytest.raises(ValidationError, match="made_at"):
        _prediction(settled_at=MADE, outcome=0.01)
    with pytest.raises(ValidationError, match="made_at"):
        _prediction(settled_at=MADE - timedelta(days=1), outcome=0.01)


def test_forecast_confidence_bounds() -> None:
    with pytest.raises(ValidationError):
        Forecast(direction=Direction.SHORT, confidence=1.5, returns=_forecast().returns)


def test_lesson_fields() -> None:
    lesson = Lesson(
        id=new_id(IdPrefix.LESSON),
        agent="global",
        created_at=MADE,
        text="Do not fade strong post-earnings drift in small caps.",
        tags=("earnings", "momentum"),
    )
    assert lesson.source_episode_ids == ()
    assert lesson.quality_score is None
    with pytest.raises(ValidationError):
        Lesson(id=new_id(IdPrefix.LESSON), agent="a", created_at=MADE, text=" ")
    with pytest.raises(ValidationError):
        Lesson(id=new_id(IdPrefix.LESSON), agent="a", created_at=MADE, text="t", quality_score=2)
    with pytest.raises(ValidationError):
        Lesson(id=new_id(IdPrefix.ORDER), agent="a", created_at=MADE, text="t")


def _nav(**kw: Any) -> NAVRecord:
    base: dict[str, Any] = {
        "date": date(2024, 1, 2),
        "nav": Decimal("100"),
        "cash": Decimal("60"),
        "long_market_value": Decimal("120"),
        "short_market_value": Decimal("-80"),
    }
    return NAVRecord(**{**base, **kw})


def test_nav_exposures_known_answer() -> None:
    nav = _nav(nav_per_share=Decimal("10.25"))
    assert nav.gross_exposure() == pytest.approx(2.0)
    assert nav.net_exposure() == pytest.approx(0.4)


@pytest.mark.parametrize(
    "change",
    [
        {"nav": Decimal("0")},
        {"nav": Decimal("-1")},
        {"long_market_value": Decimal("-1")},
        {"short_market_value": Decimal("1")},
        {"nav_per_share": Decimal("0")},
        {"cash": Decimal("NaN")},
    ],
)
def test_nav_rejects_signs(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _nav(**change)


def test_nav_allows_negative_cash() -> None:
    assert _nav(cash=Decimal("-5")).cash == Decimal("-5")


def test_ledger_amount_positive_decimal() -> None:
    entry = LedgerEntry(
        id=new_id(IdPrefix.LEDGER),
        as_of=date(2024, 1, 2),
        account="cash",
        side=EntrySide.CREDIT,
        amount="1852.50",
        ref=new_id(IdPrefix.FILL),
    )
    assert entry.amount == Decimal("1852.50")
    assert [s.value for s in EntrySide] == ["DEBIT", "CREDIT"]
    for bad in ("0", "-1", "NaN"):
        with pytest.raises(ValidationError):
            entry.model_validate({**entry.model_dump(), "amount": bad})
    with pytest.raises(ValidationError):
        entry.model_validate({**entry.model_dump(), "account": ""})
