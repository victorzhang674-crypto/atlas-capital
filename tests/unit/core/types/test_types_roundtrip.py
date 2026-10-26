"""Every contract round-trips through JSON unchanged and cannot be changed in place."""

import inspect
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

import atlas.core.types as types
from atlas.core.types import (
    AnalystReport,
    AssetClass,
    AssetView,
    AtlasModel,
    Bar,
    Breach,
    CapitalAllocation,
    Catalyst,
    Claim,
    ComplianceResult,
    DebateRole,
    DebateTurn,
    Direction,
    EntrySide,
    Evidence,
    Fill,
    Forecast,
    FrozenDict,
    IdPrefix,
    Instrument,
    InvestmentThesis,
    LedgerEntry,
    Lesson,
    MacroOutlook,
    NAVRecord,
    Order,
    OrderStatus,
    OrderType,
    PodProposal,
    Position,
    Prediction,
    ReturnDistribution,
    RiskDecision,
    RiskStatus,
    Severity,
    Side,
    Signal,
    TargetPortfolio,
    ThesisStatus,
    TimeInForce,
    View,
    Violation,
    new_id,
)

D = date(2024, 1, 2)
TS = datetime(2024, 1, 2, 21, 0, 0, 123000, tzinfo=UTC)
LATER = datetime(2024, 2, 1, 21, 0, tzinfo=UTC)
DIST = ReturnDistribution(p10=-0.05, p50=0.03, p90=0.11)
SIGNAL = Signal(name="valuation", direction=Direction.LONG, strength=0.6, evidence_ids=("e:1",))
VIEW = View(ticker="MSFT", exp_return=0.12, confidence=0.55, horizon_days=63)
ASSET_VIEW = AssetView(etf="TLT", direction=Direction.SHORT, conviction=0.7, horizon_days=40)
CLAIM = Claim(text="Margins expand", evidence_ids=("news:1",), strength=0.8)
BREACH = Breach(limit="max_gross", value=2.1, threshold=2.0, detail="gross 210%")
VIOLATION_B = Violation(
    rule="restricted_list",
    severity=Severity.BLOCK,
    message="TSLA is restricted",
    symbol="TSLA",
    order_id=new_id(IdPrefix.ORDER),
)
VIOLATION_W = Violation(rule="wash_sale", severity=Severity.WARN, message="re-buy within 30d")
ORDER = Order(
    id=new_id(IdPrefix.ORDER),
    symbol="BRK.B",
    side=Side.SELL,
    quantity=Decimal("150.5"),
    order_type=OrderType.LIMIT,
    time_in_force=TimeInForce.GTC,
    limit_price=Decimal("410.25"),
    status=OrderStatus.PARTIALLY_FILLED,
    filled_quantity=Decimal("50.25"),
    pods=("fundamental_ls", "systematic"),
    as_of=D,
    created_at=TS,
    parent_id=new_id(IdPrefix.ORDER),
)

SAMPLES: list[AtlasModel] = [
    Instrument(
        symbol="BRK.B",
        asset_class=AssetClass.EQUITY,
        exchange="NYSE",
        sector="Financials",
        industry="Insurance",
        currency="USD",
        is_shortable=False,
    ),
    Bar(
        symbol="AAPL",
        date=D,
        open=185.1,
        high=188.4,
        low=183.9,
        close=187.2,
        adj_close=186.75,
        volume=52_000_000.0,
    ),
    SIGNAL,
    Evidence(id="news:123", source="alpaca", as_of=TS, text="Apple beats", url="https://x.test/a"),
    AnalystReport(
        ticker="AAPL",
        as_of=D,
        analyst="fundamental",
        summary="Cheap",
        signals=(SIGNAL,),
        risks=("China",),
        confidence=0.7,
    ),
    ASSET_VIEW,
    MacroOutlook(
        as_of=D,
        regime_summary="Late cycle",
        growth="slowing",
        inflation="sticky",
        policy="on hold",
        credit="tight",
        risks=("recession",),
        asset_views=(ASSET_VIEW,),
        sector_tilts={"Energy": 0.5, "Tech": -0.25},
        risk_appetite=0.4,
        evidence=("fred:CPI:2024-01",),
    ),
    CLAIM,
    DebateTurn(
        speaker=DebateRole.BEAR,
        claims=(CLAIM,),
        concessions=("Balance sheet is strong",),
        questions_for_other_side=("What breaks the thesis?",),
    ),
    DIST,
    Catalyst(description="Q2 earnings", expected_date=date(2024, 4, 25)),
    InvestmentThesis(
        id=new_id(IdPrefix.THESIS),
        ticker="MSFT",
        as_of=D,
        direction=Direction.LONG,
        exp_return=DIST,
        horizon_days=63,
        conviction=0.65,
        catalysts=(Catalyst(description="Build conference"),),
        kill_criteria=("price_below(300)",),
        evidence=("news:1",),
        status=ThesisStatus.INVALIDATED,
    ),
    VIEW,
    PodProposal(
        id=new_id(IdPrefix.PROPOSAL),
        pod="fundamental_ls",
        as_of=D,
        views=(VIEW,),
        target_weights={"MSFT": 0.03, "BRK.B": -0.01},
        rationale="Quality at a fair price",
    ),
    CapitalAllocation(
        as_of=D,
        pod_weights={"fundamental_ls": 0.3, "systematic": 0.7},
        risk_budgets={"fundamental_ls": 0.25, "systematic": 0.75},
        rationale="risk parity tilt",
    ),
    TargetPortfolio(
        as_of=D,
        weights={"AAPL": 0.04, "XOM": -0.02},
        hedges={"SPY": -0.015},
        expected_vol=0.095,
        expected_beta=0.12,
    ),
    BREACH,
    RiskDecision(
        status=RiskStatus.RESIZE,
        adjusted_weights={"AAPL": 0.03},
        breaches=(BREACH,),
        metrics={"var_95": 0.012, "gross": 1.9},
    ),
    VIOLATION_B,
    ComplianceResult(approved_orders=(ORDER,), blocked=(VIOLATION_B,), warnings=(VIOLATION_W,)),
    ORDER,
    Fill(
        id=new_id(IdPrefix.FILL),
        order_id=ORDER.id,
        symbol="BRK.B",
        side=Side.SELL,
        quantity=Decimal("50.25"),
        price=Decimal("410.31"),
        commission=Decimal("0.2513"),
        ts=TS,
        as_of=D,
    ),
    Position(symbol="XOM", quantity=Decimal("-200"), avg_cost=Decimal("101.37"), pod="statarb"),
    Forecast(direction=Direction.SHORT, confidence=0.6, returns=DIST),
    Prediction(
        id=new_id(IdPrefix.PREDICTION),
        source_agent="research_director",
        made_at=TS,
        target="MSFT",
        horizon_days=63,
        forecast=Forecast(direction=Direction.LONG, confidence=0.6, returns=DIST),
        settled_at=LATER,
        outcome=0.021,
        score=0.16,
    ),
    Lesson(
        id=new_id(IdPrefix.LESSON),
        agent="global",
        created_at=TS,
        text="Respect post-earnings drift.",
        tags=("earnings",),
        source_episode_ids=("ep_1",),
        quality_score=0.8,
    ),
    NAVRecord(
        date=D,
        nav=Decimal("10012345.67"),
        cash=Decimal("-1500.10"),
        long_market_value=Decimal("12000000.00"),
        short_market_value=Decimal("-8000000.00"),
        nav_per_share=Decimal("100.1234567"),
    ),
    LedgerEntry(
        id=new_id(IdPrefix.LEDGER),
        as_of=D,
        account="commissions",
        side=EntrySide.DEBIT,
        amount=Decimal("0.2513"),
        ref="fill_x",
    ),
]


def _exported_models() -> set[type[AtlasModel]]:
    classes = (getattr(types, name) for name in types.__all__)
    models = {c for c in classes if inspect.isclass(c) and issubclass(c, AtlasModel)}
    return models - {AtlasModel}


def test_every_model_has_a_sample() -> None:
    sampled = [type(s) for s in SAMPLES]
    assert len(sampled) == len(set(sampled))
    assert set(sampled) == _exported_models()


@pytest.mark.parametrize("model", SAMPLES, ids=lambda m: type(m).__name__)
def test_json_roundtrip(model: AtlasModel) -> None:
    cls = type(model)
    assert cls.model_validate_json(model.model_dump_json()) == model
    assert cls.model_validate(model.model_dump(mode="json")) == model
    assert cls.model_validate(model.model_dump()) == model


@pytest.mark.parametrize("model", SAMPLES, ids=lambda m: type(m).__name__)
def test_models_and_maps_are_immutable(model: AtlasModel) -> None:
    first_field = next(iter(type(model).model_fields))
    with pytest.raises(ValidationError):
        setattr(model, first_field, getattr(model, first_field))
    for value in model.__dict__.values():
        assert not isinstance(value, (list, dict, set)), type(value)
        if isinstance(value, FrozenDict):
            with pytest.raises(TypeError):
                value["X"] = 0.0  # type: ignore[index]
    assert hash(model) == hash(type(model).model_validate_json(model.model_dump_json()))


def test_default_maps_are_frozen() -> None:
    tp = TargetPortfolio(as_of=D, weights={})
    decision = RiskDecision(status=RiskStatus.APPROVE, adjusted_weights={})
    assert isinstance(tp.hedges, FrozenDict)
    assert isinstance(decision.metrics, FrozenDict)


@pytest.mark.parametrize("cls", [InvestmentThesis, Order, Fill, Prediction, Lesson, LedgerEntry])
def test_roundtrip_missing_id_fails(cls: type[AtlasModel]) -> None:
    sample = next(s for s in SAMPLES if type(s) is cls)
    dumped = sample.model_dump(mode="json")
    del dumped["id"]
    with pytest.raises(ValidationError):
        cls.model_validate(dumped)


def test_roundtrip_wrong_id_prefix_fails() -> None:
    sample = next(s for s in SAMPLES if type(s) is InvestmentThesis)
    dumped = {**sample.model_dump(mode="json"), "id": new_id(IdPrefix.ORDER)}
    with pytest.raises(ValidationError):
        InvestmentThesis.model_validate(dumped)


def test_decimal_digits_survive_json() -> None:
    nav = next(s for s in SAMPLES if type(s) is NAVRecord)
    assert '"100.1234567"' in nav.model_dump_json()
