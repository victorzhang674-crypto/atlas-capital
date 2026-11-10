"""Hard budgets: the token cap, the daily cap and the per-run cap (brief task 4)."""

import threading
from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest
from llm_helpers import OFF_PEAK, FixedClock

from atlas.core.config import AtlasConfig, LLMBudgets, LLMConfig
from atlas.core.errors import BudgetExceeded, LLMError, LLMTransientError
from atlas.core.run_context import run_context
from atlas.llm import FakeLLM, FakeReply, LLMGateway
from atlas.llm.budget import BudgetGuard
from atlas.llm.cost import CostLedger
from atlas.llm.schemas import Message, ProviderRequest, ProviderResponse, Usage

MakeGateway = Callable[..., LLMGateway]

# Off-peak deepseek-flash: 1M input tokens = 0.15 USD, 1M output tokens = 0.60 USD.
ONE_DOLLAR = FakeReply(content="ok", usage=Usage(input_tokens=2_000_000, output_tokens=1_166_667))


def config(**budgets: float) -> AtlasConfig:
    values = {"daily_usd": 5.0, "per_run_usd": 25.0, **budgets}
    return AtlasConfig(llm=LLMConfig(budgets=LLMBudgets(**values)))  # type: ignore[arg-type]


def test_reply_helper_costs_one_dollar(make_gateway: MakeGateway) -> None:
    result = make_gateway(FakeLLM(default=ONE_DOLLAR)).complete([Message.user("hi")])
    assert result.cost_usd == pytest.approx(1.0, abs=1e-6)


def test_max_tokens_above_the_cap_is_refused(make_gateway: MakeGateway) -> None:
    fake = FakeLLM(default="ok")
    with pytest.raises(BudgetExceeded, match="per_agent_call_max_tokens") as excinfo:
        make_gateway(fake).complete([Message.user("hi")], max_tokens=8001)
    assert (excinfo.value.scope, excinfo.value.limit, excinfo.value.spent) == (
        "per_agent_call_max_tokens",
        8000,
        8001,
    )
    assert fake.calls == []
    make_gateway(fake).complete([Message.user("hi")], max_tokens=8000)  # the cap itself is fine
    assert fake.calls[0].max_tokens == 8000


def test_budget_exceeded_is_an_llm_error_so_callers_fail_safe(make_gateway: MakeGateway) -> None:
    with pytest.raises(LLMError):
        make_gateway(FakeLLM(default="ok")).complete([Message.user("hi")], max_tokens=10**6)


def test_daily_budget_stops_calls_and_resets_next_utc_day(
    make_gateway: MakeGateway, clock: FixedClock
) -> None:
    fake = FakeLLM(default=ONE_DOLLAR)
    gateway = make_gateway(fake, config(daily_usd=2.5))
    gateway.complete([Message.user("hi")])
    gateway.complete([Message.user("hi")])
    gateway.complete([Message.user("hi")])  # 2 USD spent + a tiny estimate is still under 2.5
    with pytest.raises(BudgetExceeded, match="daily_usd") as excinfo:
        gateway.complete([Message.user("hi")])  # 3 USD spent
    assert excinfo.value.scope == "daily_usd"
    assert excinfo.value.limit == 2.5
    assert excinfo.value.spent == pytest.approx(3.0, abs=1e-5)
    assert len(fake.calls) == 3  # the refused call never reached the provider
    clock.ts = datetime(2026, 11, 8, 0, 0, 1, tzinfo=UTC)  # the next UTC day
    assert gateway.complete([Message.user("hi")]).content == "ok"
    assert gateway.ledger.spent_on(date(2026, 11, 7)) == pytest.approx(3.0, abs=1e-5)
    assert gateway.ledger.spent_on(date(2026, 11, 8)) == pytest.approx(1.0, abs=1e-5)


def test_daily_budget_uses_the_utc_day_of_the_wall_clock(
    make_gateway: MakeGateway, clock: FixedClock
) -> None:
    gateway = make_gateway(FakeLLM(default=ONE_DOLLAR), config(daily_usd=1.5))
    clock.ts = datetime(2026, 11, 7, 23, 59, 59, tzinfo=UTC)
    gateway.complete([Message.user("hi")])
    gateway.complete([Message.user("hi")])
    with pytest.raises(BudgetExceeded):
        gateway.complete([Message.user("hi")])
    clock.ts = datetime(2026, 11, 8, 0, 0, 0, tzinfo=UTC)
    gateway.complete([Message.user("hi")])


def test_per_run_budget_is_per_run_id(make_gateway: MakeGateway) -> None:
    fake = FakeLLM(default=ONE_DOLLAR)
    gateway = make_gateway(fake, config(daily_usd=100.0, per_run_usd=1.5))
    with run_context() as first:
        gateway.complete([Message.user("hi")])
        gateway.complete([Message.user("hi")])
        with pytest.raises(BudgetExceeded, match="per_run_usd") as excinfo:
            gateway.complete([Message.user("hi")])
        assert excinfo.value.scope == "per_run_usd"
        assert excinfo.value.spent == pytest.approx(2.0, abs=1e-5)
    with run_context() as second:
        assert gateway.complete([Message.user("hi")]).content == "ok"  # a new run starts at zero
    assert first.run_id != second.run_id
    assert gateway.ledger.spent_in_run(first.run_id) == pytest.approx(2.0, abs=1e-5)
    assert gateway.ledger.spent_in_run(second.run_id) == pytest.approx(1.0, abs=1e-5)


def test_calls_without_a_run_share_one_bucket(make_gateway: MakeGateway) -> None:
    gateway = make_gateway(FakeLLM(default=ONE_DOLLAR), config(daily_usd=100.0, per_run_usd=1.5))
    gateway.complete([Message.user("hi")])
    gateway.complete([Message.user("hi")])
    with pytest.raises(BudgetExceeded, match="per_run_usd"):
        gateway.complete([Message.user("hi")])
    with run_context():
        gateway.complete([Message.user("hi")])  # a real run is not charged for run-less calls
    assert gateway.ledger.spent_in_run(None) == pytest.approx(2.0, abs=1e-5)


def test_the_estimate_alone_can_refuse_a_call(make_gateway: MakeGateway) -> None:
    # deep tier off-peak: output 1.98 USD per 1M tokens; 8000 tokens = 0.01584 USD worst case.
    fake = FakeLLM(default="ok")
    gateway = make_gateway(fake, config(daily_usd=0.01))
    with pytest.raises(BudgetExceeded, match="daily_usd") as excinfo:
        gateway.complete([Message.user("hi")], tier="deep")
    assert excinfo.value.spent == 0.0  # nothing spent: the worst case alone does not fit
    assert fake.calls == []
    assert gateway.complete([Message.user("hi")], tier="deep", max_tokens=1000).content == "ok"


def test_estimate_counts_prompt_characters() -> None:
    guard = BudgetGuard(LLMBudgets(estimate_chars_per_token=3.0), CostLedger())
    request = ProviderRequest(
        model="deepseek-flash",
        tier="quick",
        messages=(Message.system("x" * 2999), Message.user("y" * 1)),
        max_tokens=2000,
        timeout_seconds=1.0,
    )
    rates = LLMConfig().prices["deepseek-flash"]
    # 3000 characters / 3 = 1000 prompt tokens x 0.15 + 2000 output tokens x 0.6 = 1350 per 1M
    assert guard.estimate_usd(request, rates, OFF_PEAK) == pytest.approx(1350e-6, rel=1e-12)
    longer = ProviderRequest(
        model="deepseek-flash",
        tier="quick",
        messages=(Message.user("y" * 3001),),
        max_tokens=2000,
        timeout_seconds=1.0,
    )
    # 3001 characters round up to 1001 tokens
    assert guard.estimate_usd(longer, rates, OFF_PEAK) == pytest.approx(1350.15e-6, rel=1e-12)


class BlockingProvider:
    """A provider whose calls wait until the test releases them."""

    name = "blocking"

    def __init__(self) -> None:
        self.entered = threading.Semaphore(0)
        self.release = threading.Event()
        self.calls = 0

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        self.entered.release()
        assert self.release.wait(timeout=5)
        return ProviderResponse(content="ok", usage=Usage(output_tokens=1), model=request.model)


def test_in_flight_calls_reserve_budget(make_gateway: MakeGateway) -> None:  # review focus 5
    # Each deep call reserves about 0.01584 USD (8000 output tokens); the cap fits two.
    provider = BlockingProvider()
    gateway = make_gateway(provider, config(daily_usd=0.04))
    outcomes: list[str] = []

    def call() -> None:
        try:
            gateway.complete([Message.user("hi")], tier="deep")
            outcomes.append("ok")
        except BudgetExceeded:
            outcomes.append("refused")

    first, second = threading.Thread(target=call), threading.Thread(target=call)
    first.start()
    second.start()
    assert provider.entered.acquire(timeout=5)
    assert provider.entered.acquire(timeout=5)  # two calls are in flight, nothing is spent yet
    with pytest.raises(BudgetExceeded, match="daily_usd"):
        gateway.complete([Message.user("hi")], tier="deep")  # the third would overshoot
    assert provider.calls == 2
    provider.release.set()
    first.join(timeout=5)
    second.join(timeout=5)
    assert outcomes == ["ok", "ok"]
    # The reservations are released: the tiny real cost leaves room for more calls.
    assert gateway.complete([Message.user("hi")], tier="deep").content == "ok"


def test_reservation_is_released_when_the_call_fails(make_gateway: MakeGateway) -> None:
    fake = FakeLLM()
    fake.add(None, None, LLMError("401"), *[LLMTransientError("503")] * 4, "ok", "ok")
    gateway = make_gateway(fake, config(daily_usd=0.02))  # fits one deep reservation only
    with pytest.raises(LLMError, match="401"):
        gateway.complete([Message.user("hi")], tier="deep")
    with pytest.raises(LLMError, match="failed after 4 attempts"):
        gateway.complete([Message.user("hi")], tier="deep")
    assert gateway.complete([Message.user("hi")], tier="deep").content == "ok"
    assert gateway.complete([Message.user("hi")], tier="deep").content == "ok"


def test_repair_retry_is_budget_checked_too(make_gateway: MakeGateway) -> None:
    from pydantic import BaseModel

    class Verdict(BaseModel):
        rating: str

    fake = FakeLLM()
    fake.add(None, None, FakeReply(content="not json", usage=ONE_DOLLAR.usage), '{"rating": "buy"}')
    gateway = make_gateway(fake, config(daily_usd=0.9))
    with pytest.raises(BudgetExceeded, match="daily_usd"):
        gateway.complete([Message.user("hi")], schema=Verdict)
    assert len(fake.calls) == 1  # the first answer cost 1 USD; the repair call was refused
    assert gateway.ledger.spent_on(OFF_PEAK.date()) == pytest.approx(1.0, abs=1e-5)


def test_ledger_totals_per_day_run_and_agent(make_gateway: MakeGateway, clock: FixedClock) -> None:
    gateway = make_gateway(FakeLLM(default=ONE_DOLLAR), config(daily_usd=100.0, per_run_usd=100.0))
    with run_context() as run:
        gateway.complete([Message.user("hi")], metadata={"agent": "analyst", "step": "draft"})
        gateway.complete([Message.user("hi")], metadata={"agent": "analyst", "step": "final"})
        gateway.complete([Message.user("hi")], metadata={"agent": "pm"})
    clock.ts = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
    gateway.complete([Message.user("hi")], metadata={"agent": "pm"})
    ledger = gateway.ledger
    assert ledger.spent_on(date(2026, 11, 7)) == pytest.approx(3.0, abs=1e-5)
    assert ledger.spent_in_run(run.run_id) == pytest.approx(3.0, abs=1e-5)
    assert ledger.spent_by_agent("analyst") == pytest.approx(2.0, abs=1e-5)
    assert ledger.spent_by_agent("pm") == pytest.approx(2.0, abs=1e-5)
    assert ledger.spent_by_agent("pm", day=date(2026, 11, 8)) == pytest.approx(1.0, abs=1e-5)
    assert ledger.spent_by_agent("nobody") == 0.0
    records = ledger.records()
    assert len(records) == 4
    assert [r.agent for r in records] == ["analyst", "analyst", "pm", "pm"]
    assert [r.step for r in records] == ["draft", "final", None, None]
    assert [r.run_id for r in records] == [run.run_id] * 3 + [None]
    assert len({r.call_id for r in records}) == 4


def test_every_billed_response_is_one_ledger_row(make_gateway: MakeGateway) -> None:
    from pydantic import BaseModel

    class Verdict(BaseModel):
        rating: str

    fake = FakeLLM()
    fake.add(
        "analyst",
        "rate",
        FakeReply(content="not json", usage=Usage(input_tokens=100, output_tokens=20)),
        FakeReply(content="still not json", usage=Usage(input_tokens=150, output_tokens=30)),
    )
    gateway = make_gateway(fake)
    with pytest.raises(LLMError, match="Verdict"):
        gateway.complete(
            [Message.user("hi")], schema=Verdict, metadata={"agent": "analyst", "step": "rate"}
        )
    first, second = gateway.ledger.records()  # both answers were billed, so both are logged
    assert (first.attempt, first.status, first.input_tokens, first.output_tokens) == (
        1,
        "invalid_output",
        100,
        20,
    )
    assert (second.attempt, second.status) == (2, "invalid_output")
    assert first.cost_usd == pytest.approx((100 * 0.15 + 20 * 0.6) / 1e6, rel=1e-12)
    assert (first.tier, first.provider, first.model) == ("quick", "fake", "deepseek-flash")
    assert first.ts == OFF_PEAK
    assert first.day == OFF_PEAK.date()
    assert first.latency_ms == pytest.approx(250.0)
    assert first.cache_hit is False


# --- Audit (mutation survivors) ------------------------------------------------------------


def refused(guard: BudgetGuard, usd: float, run_id: str | None) -> str | None:
    """The scope that refuses a reservation of `usd` today, or `None` if it is granted."""
    try:
        with guard.reserve(usd, day=OFF_PEAK.date(), run_id=run_id):
            return None
    except BudgetExceeded as exc:
        return exc.scope


def test_spending_exactly_up_to_the_cap_is_allowed() -> None:
    guard = BudgetGuard(LLMBudgets(daily_usd=1.0, per_run_usd=1.0), CostLedger())
    assert refused(guard, 1.0, None) is None  # spent 0 + estimate 1.0 == cap: allowed
    with guard.reserve(1.0, day=OFF_PEAK.date(), run_id=None):
        assert refused(guard, 0.000001, None) == "daily_usd"  # anything more is over the cap


def test_releasing_one_reservation_keeps_the_others() -> None:
    guard = BudgetGuard(LLMBudgets(daily_usd=1.0, per_run_usd=100.0), CostLedger())
    with guard.reserve(0.5, day=OFF_PEAK.date(), run_id="a"):
        assert refused(guard, 0.3, "b") is None  # granted and released again
        # 0.5 is still held: 0.5 + 0.6 would overshoot the daily cap, 0.5 + 0.5 just fits.
        assert refused(guard, 0.6, "b") == "daily_usd"
        assert refused(guard, 0.5, "b") is None
    assert refused(guard, 1.0, "b") is None  # everything released


def test_per_run_reservations_are_separate_per_run() -> None:
    guard = BudgetGuard(LLMBudgets(daily_usd=100.0, per_run_usd=1.0), CostLedger())
    with guard.reserve(0.8, day=OFF_PEAK.date(), run_id="a"):
        assert refused(guard, 0.8, "b") is None  # another run has its own cap
        assert refused(guard, 0.8, "a") == "per_run_usd"
        assert refused(guard, 0.2, "a") is None


def test_ledger_row_keeps_cache_write_tokens(make_gateway: MakeGateway) -> None:
    usage = Usage(
        input_tokens=1,
        cached_input_tokens=2,
        cache_write_tokens=3,
        output_tokens=5,
        reasoning_tokens=4,
    )
    gateway = make_gateway(FakeLLM(default=FakeReply(content="ok", usage=usage)))
    gateway.complete([Message.user("hi")])
    (row,) = gateway.ledger.records()
    assert (
        row.input_tokens,
        row.cached_input_tokens,
        row.cache_write_tokens,
        row.output_tokens,
        row.reasoning_tokens,
    ) == (1, 2, 3, 5, 4)
