"""Costing from the config price table, including DeepSeek's UTC peak windows."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone

import pytest
from llm_helpers import OFF_PEAK, PEAK, FixedClock

from atlas.core.config import AtlasConfig, LLMConfig, LLMTiers, ModelPrice
from atlas.llm import FakeLLM, FakeReply, LLMGateway
from atlas.llm.cost import Rates, cost_usd, rates_at
from atlas.llm.schemas import Message, Usage

MakeGateway = Callable[..., LLMGateway]

PRICES = LLMConfig().prices
FLASH, PRO, OPUS = PRICES["deepseek-flash"], PRICES["deepseek-v4-pro"], PRICES["claude-opus-5-5"]
USAGE = Usage(input_tokens=1000, cached_input_tokens=4000, output_tokens=500, reasoning_tokens=100)


def utc(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    """An instant in November 2026 (the 9th is a Monday, the 7th a Saturday)."""
    return datetime(2026, 11, day, hour, minute, second, tzinfo=UTC)


def test_off_peak_cost_by_hand() -> None:
    # flash off-peak: 1000 x 0.15 + 4000 x 0.003 + 500 x 0.6 = 150 + 12 + 300 = 462 USD per 1M
    assert cost_usd(USAGE, FLASH, OFF_PEAK) == pytest.approx(462e-6, rel=1e-12)
    # pro off-peak: 1000 x 0.66 + 4000 x 0.022 + 500 x 1.98 = 660 + 88 + 990 = 1738
    assert cost_usd(USAGE, PRO, OFF_PEAK) == pytest.approx(1738e-6, rel=1e-12)


def test_peak_cost_is_double() -> None:
    assert cost_usd(USAGE, FLASH, PEAK) == pytest.approx(924e-6, rel=1e-12)
    assert cost_usd(USAGE, PRO, PEAK) == pytest.approx(3476e-6, rel=1e-12)


@pytest.mark.parametrize(
    ("ts", "peak"),
    [
        (utc(9, 0, 59, 59), False),  # Monday, just before the first window
        (utc(9, 1, 0), True),  # start is inclusive
        (utc(9, 3, 59, 59), True),
        (utc(9, 4, 0), False),  # end is exclusive
        (utc(9, 5, 0), False),  # between the two windows
        (utc(9, 6, 0), True),
        (utc(9, 9, 59, 59), True),
        (utc(9, 10, 0), False),
        (utc(13, 2, 0), True),  # Friday
        (utc(7, 2, 0), False),  # Saturday
        (utc(8, 7, 0), False),  # Sunday
    ],
)
def test_peak_window_edges(ts: datetime, peak: bool) -> None:
    assert rates_at(FLASH, ts).output == (1.2 if peak else 0.6)
    assert rates_at(PRO, ts).input == (1.32 if peak else 0.66)


def test_windows_are_read_in_utc_whatever_the_offset() -> None:
    shanghai = timezone(timedelta(hours=8))
    # 10:30 in Shanghai on Monday is 02:30 UTC: peak.
    assert rates_at(FLASH, datetime(2026, 11, 9, 10, 30, tzinfo=shanghai)).output == 1.2
    # 02:30 in Shanghai on Monday is 18:30 UTC on Sunday: off-peak.
    assert rates_at(FLASH, datetime(2026, 11, 9, 2, 30, tzinfo=shanghai)).output == 0.6


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        rates_at(FLASH, datetime(2026, 11, 9, 2, 30))
    with pytest.raises(ValueError, match="timezone-aware"):
        cost_usd(USAGE, FLASH, datetime(2026, 11, 9, 2, 30))


def test_rates_resolve_missing_cache_prices_to_the_input_rate() -> None:
    plain = ModelPrice(input=2.0, output=8.0)
    assert rates_at(plain, OFF_PEAK) == Rates(
        input=2.0, cached_input=2.0, cache_write=2.0, output=8.0
    )
    assert rates_at(FLASH, OFF_PEAK) == Rates(
        input=0.15, cached_input=0.003, cache_write=0.15, output=0.6
    )
    assert rates_at(FLASH, PEAK) == Rates(
        input=0.3, cached_input=0.006, cache_write=0.3, output=1.2
    )


def test_cache_write_tokens_are_charged() -> None:
    usage = Usage(
        input_tokens=100, cached_input_tokens=200, cache_write_tokens=300, output_tokens=50
    )
    # opus: 100 x 4 + 200 x 0.2 + 300 x 5 + 50 x 20 = 400 + 40 + 1500 + 1000 = 2940
    assert cost_usd(usage, OPUS, OFF_PEAK) == pytest.approx(2940e-6, rel=1e-12)


def test_reasoning_tokens_are_not_charged_twice() -> None:
    with_reasoning = Usage(output_tokens=500, reasoning_tokens=400)
    assert cost_usd(with_reasoning, FLASH, OFF_PEAK) == cost_usd(
        Usage(output_tokens=500), FLASH, OFF_PEAK
    )


def test_zero_usage_costs_nothing() -> None:
    assert cost_usd(Usage(), PRO, PEAK) == 0.0


def test_gateway_prices_at_the_wall_clock_instant(make_gateway: MakeGateway) -> None:
    reply = FakeReply(content="ok", usage=USAGE)
    off_peak = make_gateway(FakeLLM(default=reply), clock=FixedClock(OFF_PEAK))
    peak = make_gateway(FakeLLM(default=reply), clock=FixedClock(PEAK))
    assert off_peak.complete([Message.user("hi")]).cost_usd == pytest.approx(462e-6, rel=1e-12)
    assert peak.complete([Message.user("hi")]).cost_usd == pytest.approx(924e-6, rel=1e-12)


def test_unpriced_model_never_reaches_the_provider(make_gateway: MakeGateway) -> None:
    # review focus 3: config validation already refuses an unpriced tier model ...
    with pytest.raises(ValueError, match="no price"):
        LLMConfig(tiers=LLMTiers(quick="brand-new-model"))
    # ... and a config built around the validator still cannot produce an uncosted call.
    llm = LLMConfig.model_construct(
        **{**dict(LLMConfig()), "tiers": LLMTiers(quick="brand-new-model")}
    )
    config = AtlasConfig.model_construct(**{**dict(AtlasConfig()), "llm": llm})
    fake = FakeLLM(default="ok")
    from atlas.core.errors import ConfigError

    with pytest.raises(ConfigError, match=r"brand-new-model.*llm\.prices"):
        make_gateway(fake, config).complete([Message.user("hi")])
    assert fake.calls == []
