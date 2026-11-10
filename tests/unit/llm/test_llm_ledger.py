"""Usage persistence (brief task 5): the ledger on DuckDB `llm_usage`."""

from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest
from llm_helpers import OFF_PEAK, FixedClock
from pydantic import BaseModel, SecretStr

from atlas.core.config import AtlasConfig, LLMBudgets, LLMConfig, Secrets
from atlas.core.errors import BudgetExceeded, LLMError
from atlas.core.run_context import run_context
from atlas.core.storage import Database, Lake, get_database
from atlas.llm import FakeLLM, FakeReply, LLMGateway, build_gateway
from atlas.llm.cost import CostLedger, UsageRecord
from atlas.llm.schemas import Message, Usage

MakeGateway = Callable[..., LLMGateway]

ONE_DOLLAR = FakeReply(content="ok", usage=Usage(input_tokens=2_000_000, output_tokens=1_166_667))


def record(call_id: str, **overrides: object) -> UsageRecord:
    values: dict[str, object] = {
        "call_id": call_id,
        "ts": OFF_PEAK,
        "day": OFF_PEAK.date(),
        "run_id": "01RUN",
        "agent": "analyst",
        "step": "draft",
        "tier": "quick",
        "provider": "fake",
        "model": "deepseek-flash",
        "input_tokens": 10,
        "output_tokens": 5,
        "cost_usd": 1.0,
        "latency_ms": 3.0,
    }
    values.update(overrides)
    return UsageRecord(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("backed", [False, True])
def test_memory_and_database_ledgers_agree(tmp_lake: Lake, backed: bool) -> None:
    ledger = CostLedger(Database(tmp_lake)) if backed else CostLedger()
    other_day = datetime(2026, 11, 8, 3, 0, tzinfo=UTC)
    rows = [
        record("a"),
        record("b", agent="pm", step=None, cost_usd=2.0),
        record("c", run_id=None, cost_usd=4.0),
        record("d", ts=other_day, day=other_day.date(), cost_usd=8.0, status="invalid_output"),
    ]
    for item in rows:
        ledger.record(item)
    assert ledger.records() == tuple(rows)  # same values, in the order written
    assert ledger.spent_on(OFF_PEAK.date()) == 7.0
    assert ledger.spent_on(date(2026, 11, 8)) == 8.0
    assert ledger.spent_on(date(2020, 1, 1)) == 0.0
    assert ledger.spent_in_run("01RUN") == 11.0
    assert ledger.spent_in_run(None) == 4.0
    assert ledger.spent_in_run("01NOPE") == 0.0
    assert ledger.spent_by_agent("analyst") == 13.0
    assert ledger.spent_by_agent("analyst", day=OFF_PEAK.date()) == 5.0
    assert ledger.spent_by_agent("pm") == 2.0


def test_ledger_survives_reopening_the_database(tmp_lake: Lake) -> None:
    with Database(tmp_lake) as first:
        CostLedger(first).record(record("a", cost_usd=1.25))
    with Database(tmp_lake) as second:
        ledger = CostLedger(second)
        assert ledger.spent_on(OFF_PEAK.date()) == 1.25
        assert ledger.records() == (record("a", cost_usd=1.25),)


def test_gateway_writes_one_row_per_provider_response(
    tmp_lake: Lake, make_gateway: MakeGateway
) -> None:
    class Verdict(BaseModel):
        rating: str

    database = Database(tmp_lake)
    fake = FakeLLM()
    fake.add(
        "analyst",
        "rate",
        FakeReply(content="not json", usage=Usage(input_tokens=100, output_tokens=20)),
        FakeReply(
            content='{"rating": "buy"}',
            usage=Usage(
                input_tokens=150, cached_input_tokens=40, output_tokens=30, reasoning_tokens=10
            ),
        ),
    )
    gateway = make_gateway(fake, ledger=CostLedger(database))
    with run_context() as run:
        result = gateway.complete(
            [Message.user("rate AAA")],
            tier="deep",
            schema=Verdict,
            metadata={"agent": "analyst", "step": "rate"},
        )
    frame = database.query("select * from llm_usage order by attempt")
    assert len(frame) == 2
    assert frame["run_id"].tolist() == [run.run_id] * 2
    assert frame["agent"].tolist() == ["analyst"] * 2
    assert frame["step"].tolist() == ["rate"] * 2
    assert frame["tier"].tolist() == ["deep"] * 2
    assert frame["provider"].tolist() == ["fake"] * 2
    assert frame["model"].tolist() == ["deepseek-v4-pro"] * 2
    assert frame["attempt"].tolist() == [1, 2]
    assert frame["status"].tolist() == ["invalid_output", "ok"]
    assert frame["input_tokens"].tolist() == [100, 150]
    assert frame["cached_input_tokens"].tolist() == [0, 40]
    assert frame["output_tokens"].tolist() == [20, 30]
    assert frame["reasoning_tokens"].tolist() == [0, 10]
    assert frame["cache_hit"].tolist() == [False, False]
    assert frame["day"].tolist() == [OFF_PEAK.date()] * 2
    # Every call is costed and logged: the rows add up to the result's cost.
    assert frame["cost_usd"].sum() == pytest.approx(result.cost_usd, rel=1e-12)
    # deep off-peak: (100 x 0.66 + 20 x 1.98) and (150 x 0.66 + 40 x 0.022 + 30 x 1.98) per 1M
    assert frame["cost_usd"].tolist() == pytest.approx([105.6e-6, 159.28e-6], rel=1e-9)
    database.close()


def test_budget_sees_spend_from_an_earlier_session(tmp_lake: Lake, cfg: AtlasConfig) -> None:
    config = AtlasConfig(llm=LLMConfig(budgets=LLMBudgets(daily_usd=1.5)))
    clock = FixedClock()
    with Database(tmp_lake) as first:
        earlier = LLMGateway(
            FakeLLM(default=ONE_DOLLAR), config, ledger=CostLedger(first), clock=clock
        )
        earlier.complete([Message.user("hi")])
        earlier.complete([Message.user("hi")])
    with Database(tmp_lake) as second:  # a new process on the same day
        fake = FakeLLM(default=ONE_DOLLAR)
        later = LLMGateway(fake, config, ledger=CostLedger(second), clock=clock)
        with pytest.raises(BudgetExceeded, match="daily_usd") as excinfo:
            later.complete([Message.user("hi")])
        assert excinfo.value.spent == pytest.approx(2.0, abs=1e-5)
        assert fake.calls == []
        clock.ts = datetime(2026, 11, 8, 0, 0, 1, tzinfo=UTC)
        assert later.complete([Message.user("hi")]).content == "ok"


def test_failed_call_writes_no_row(tmp_lake: Lake, make_gateway: MakeGateway) -> None:
    database = Database(tmp_lake)
    gateway = make_gateway(FakeLLM(default=LLMError("401")), ledger=CostLedger(database))
    with pytest.raises(LLMError):
        gateway.complete([Message.user("hi")])
    assert len(database.query("select * from llm_usage")) == 0  # nothing was billed
    database.close()


def test_build_gateway_with_a_lake_persists_usage(tmp_lake: Lake) -> None:
    config = AtlasConfig(secrets=Secrets(deepseek_api_key=SecretStr("sk-test-not-real")))
    gateway = build_gateway(config, lake=tmp_lake)
    gateway.ledger.record(record("a", cost_usd=0.5))
    database = get_database(tmp_lake)
    assert database.query("select cost_usd from llm_usage")["cost_usd"].tolist() == [0.5]
    assert build_gateway(config).ledger.records() == ()  # no lake: a fresh in-memory ledger
    database.close()
