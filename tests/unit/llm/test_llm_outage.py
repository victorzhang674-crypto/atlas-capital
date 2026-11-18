"""Fail fast while the vendor is down (Phase 3 checkpoint Tier 2 item 6).

One call that times out cost `request_timeout_seconds * (max_retries + 1)` plus backoff (about
8 minutes with the defaults), and every agent call of the day paid it again: a dead vendor took
hours to drop the day's names. Now `llm.retry.outage_failures` transient failures in a row
(across the gateway's calls) mark the vendor down for `outage_cooldown_seconds`: calls in that
window fail at once with `LLMError` (no provider call, no wait). A rate limit with a
`Retry-After` is not an outage (the vendor answered) and does not count. Any answer resets the
count; after the cool-down the next call tries again.
"""

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from atlas.core.config import AtlasConfig, LLMConfig, LLMRetry
from atlas.core.errors import LLMError, LLMTransientError
from atlas.llm import FakeLLM, LLMGateway
from atlas.llm.schemas import Message

MakeGateway = Callable[..., LLMGateway]
DOWN = LLMTransientError("timed out")


class Monotonic:
    """A settable monotonic clock, in seconds."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def config(failures: int = 4, cooldown: float = 60.0, retries: int = 1) -> AtlasConfig:
    retry = LLMRetry(
        max_retries=retries, outage_failures=failures, outage_cooldown_seconds=cooldown
    )
    return AtlasConfig(llm=LLMConfig(retry=retry))


def ask(gateway: LLMGateway) -> str:
    return gateway.complete([Message.user("hi")]).content


def test_defaults() -> None:
    retry = LLMRetry()
    assert retry.outage_failures == 8
    assert retry.outage_cooldown_seconds == pytest.approx(300.0)


@pytest.mark.parametrize(
    "bad", [{"outage_failures": -1}, {"outage_cooldown_seconds": 0.0}], ids=str
)
def test_config_is_validated(bad: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        LLMRetry(**bad)  # type: ignore[arg-type]


def test_consecutive_failures_open_the_cool_down(
    make_gateway: MakeGateway, sleeps: list[float]
) -> None:
    fake = FakeLLM(default=DOWN)
    clock = Monotonic()
    gateway = make_gateway(fake, config(), monotonic=clock)
    with pytest.raises(LLMError, match="failed after 2 attempts"):
        ask(gateway)  # 2 failures
    with pytest.raises(LLMError, match="marked down"):
        ask(gateway)  # 2 more: the 4th marks the vendor down and is reported as such
    assert len(fake.calls) == 4
    sleeps.clear()
    for _ in range(5):
        with pytest.raises(LLMError, match=r"marked down .*4 transient failures in a row"):
            ask(gateway)
    assert len(fake.calls) == 4  # no provider call while down
    assert sleeps == []  # and no wait


def test_the_breaker_stops_a_call_mid_retries(
    make_gateway: MakeGateway, sleeps: list[float]
) -> None:
    fake = FakeLLM(default=DOWN)
    gateway = make_gateway(fake, config(failures=2, retries=5), monotonic=Monotonic())
    with pytest.raises(LLMError, match="marked down"):
        ask(gateway)
    assert len(fake.calls) == 2  # not 6: the second failure opened the cool-down
    assert sleeps == [1.0]  # no wait once the vendor is down


def test_after_the_cool_down_the_next_call_tries_again(make_gateway: MakeGateway) -> None:
    fake = FakeLLM()
    fake.add(None, None, DOWN, DOWN, DOWN, DOWN, "back")
    clock = Monotonic()
    gateway = make_gateway(fake, config(), monotonic=clock)
    for _ in range(2):
        with pytest.raises(LLMError):
            ask(gateway)
    clock.now += 59.0
    with pytest.raises(LLMError, match="marked down"):
        ask(gateway)
    clock.now += 1.0
    assert ask(gateway) == "back"
    assert len(fake.calls) == 5


def test_a_failure_after_the_cool_down_reopens_it_at_once(make_gateway: MakeGateway) -> None:
    fake = FakeLLM(default=DOWN)
    clock = Monotonic()
    gateway = make_gateway(fake, config(retries=3), monotonic=clock)
    with pytest.raises(LLMError, match="marked down"):
        ask(gateway)  # 4 failures
    clock.now += 60.0
    with pytest.raises(LLMError, match="marked down"):
        ask(gateway)  # one probe attempt, then down again
    assert len(fake.calls) == 5


def test_an_answer_resets_the_count(make_gateway: MakeGateway) -> None:
    fake = FakeLLM()
    fake.add(None, None, DOWN, DOWN, "ok", DOWN, DOWN, "ok")
    gateway = make_gateway(fake, config(failures=3, retries=2), monotonic=Monotonic())
    assert ask(gateway) == "ok"
    assert ask(gateway) == "ok"  # 2 + 2 failures, never 3 in a row


def test_rate_limits_with_retry_after_are_not_an_outage(make_gateway: MakeGateway) -> None:
    fake = FakeLLM()
    limited = LLMTransientError("429", retry_after=1.0)
    fake.add(None, None, *[limited] * 6, "ok")
    gateway = make_gateway(fake, config(failures=2, retries=6), monotonic=Monotonic())
    assert ask(gateway) == "ok"


def test_zero_turns_the_breaker_off(make_gateway: MakeGateway) -> None:
    fake = FakeLLM(default=DOWN)
    gateway = make_gateway(fake, config(failures=0), monotonic=Monotonic())
    for _ in range(10):
        with pytest.raises(LLMError, match="failed after 2 attempts"):
            ask(gateway)
    assert len(fake.calls) == 20


def test_cache_hits_are_served_while_down(make_gateway: MakeGateway, tmp_path: object) -> None:
    from atlas.core.storage import Database, Lake
    from atlas.llm.cache import LLMCache

    fake = FakeLLM()
    fake.add(None, None, "recorded", DOWN, DOWN, DOWN, DOWN)
    database = Database(Lake(tmp_path))  # type: ignore[arg-type]
    try:
        gateway = make_gateway(
            fake, config(), monotonic=Monotonic(), cache=LLMCache(database), cache_mode="read_write"
        )
        assert ask(gateway) == "recorded"
        for _ in range(2):
            with pytest.raises(LLMError):
                gateway.complete([Message.user("something else")])
        with pytest.raises(LLMError, match="marked down"):
            gateway.complete([Message.user("a third question")])
        assert ask(gateway) == "recorded"  # a hit needs no vendor
    finally:
        database.close()
