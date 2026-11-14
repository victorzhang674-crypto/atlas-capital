"""Stage 25 post-DONE audit: the findings A1-A5, each pinned by a test (see the audit report)."""

import threading
import time
from collections.abc import Callable

import pytest
from pydantic import ValidationError
from structlog.testing import capture_logs

from atlas.core.errors import CacheMiss
from atlas.core.storage import Database, Lake
from atlas.llm import (
    CostLedger,
    FakeLLM,
    FakeReply,
    LLMGateway,
    Message,
    ProviderRequest,
    ProviderResponse,
)
from atlas.llm.cache import LLMCache
from atlas.llm.schemas import LLMResult, Usage

MakeGateway = Callable[..., LLMGateway]
ASK = [Message.user("Rate AAPL.")]
META = {"agent": "analyst", "step": "rate", "prompt_version": "v1"}


class SlowFake:
    """A FakeLLM that takes `delay` seconds per answer, so two threads overlap."""

    name = "fake"

    def __init__(self, fake: FakeLLM, delay: float = 0.05) -> None:
        self.fake, self.delay = fake, delay

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        time.sleep(self.delay)
        return self.fake.complete(request)


# A1: replay must give the results the recorded run saw (spec §3 principle 3).


def test_concurrent_identical_misses_call_once_and_agree(make_gateway: MakeGateway) -> None:
    fake = FakeLLM({"analyst": ["one", "two"]})
    gateway = make_gateway(SlowFake(fake), cache=LLMCache(), cache_mode="read_write")
    results: list[str] = []
    threads = [
        threading.Thread(
            target=lambda: results.append(gateway.complete(ASK, metadata=META).content)
        )
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(fake.calls) == 1
    assert results == ["one", "one"]


def test_a_record_run_replays_identically(make_gateway: MakeGateway) -> None:
    cache = LLMCache()
    recorder = make_gateway(FakeLLM({"analyst": ["one", "two"]}), cache=cache, cache_mode="record")
    recorded = [recorder.complete(ASK, metadata=META).content for _ in range(2)]
    replayer = make_gateway(FakeLLM(), cache=cache, cache_mode="replay_only")
    replayed = [replayer.complete(ASK, metadata=META).content for _ in range(2)]
    assert replayed == recorded


def test_record_re_records_each_key_once_per_gateway(make_gateway: MakeGateway) -> None:
    cache = LLMCache()
    make_gateway(FakeLLM({"analyst": "old"}), cache=cache, cache_mode="read_write").complete(
        ASK, metadata=META
    )
    fake = FakeLLM({"analyst": ["new", "newer"]})
    recorder = make_gateway(fake, cache=cache, cache_mode="record")
    first, again = recorder.complete(ASK, metadata=META), recorder.complete(ASK, metadata=META)
    assert (first.content, first.cache_hit) == ("new", False)  # the old recording is refreshed
    assert (again.content, again.cache_hit) == ("new", True)
    assert len(fake.calls) == 1


# A2: the token cap is sent to the provider, so it is part of the key.


def test_a_truncated_answer_is_not_replayed_under_a_larger_cap(make_gateway: MakeGateway) -> None:
    fake = FakeLLM({"analyst": [FakeReply(content="cut", finish_reason="length"), "full answer"]})
    gateway = make_gateway(fake, cache=LLMCache(), cache_mode="read_write")
    assert gateway.complete(ASK, metadata=META, max_tokens=50).finish_reason == "length"
    bigger = gateway.complete(ASK, metadata=META, max_tokens=500)
    assert (bigger.content, bigger.cache_hit) == ("full answer", False)


# A3: 1 and 1.0 are the same temperature.


def test_an_int_temperature_hits_the_float_recording(make_gateway: MakeGateway) -> None:
    fake = FakeLLM({"analyst": "buy"})
    gateway = make_gateway(fake, cache=LLMCache(), cache_mode="read_write")
    gateway.complete(ASK, metadata=META, temperature=1.0)
    assert gateway.complete(ASK, metadata=META, temperature=1).cache_hit  # type: ignore[arg-type]
    assert len(fake.calls) == 1


# A5: attempts == 0 exactly when the cache answered.


@pytest.mark.parametrize(("attempts", "hit"), [(0, False), (1, True), (2, True)])
def test_attempts_and_cache_hit_agree(attempts: int, hit: bool) -> None:
    with pytest.raises(ValidationError, match="attempts"):
        LLMResult(
            content="",
            usage=Usage(),
            cost_usd=0.0,
            model="m",
            latency_ms=0.0,
            cache_hit=hit,
            attempts=attempts,
        )


# A6 (mutation survivors): hits and replay misses are logged; hit rows get their own ids.


def test_a_hit_is_logged_with_what_it_saved(make_gateway: MakeGateway) -> None:
    reply = FakeReply(content="ok", usage=Usage(input_tokens=1000, output_tokens=500))
    gateway = make_gateway(FakeLLM({"analyst": reply}), cache=LLMCache(), cache_mode="read_write")
    paid = gateway.complete(ASK, metadata=META)
    with capture_logs() as logs:
        gateway.complete(ASK, metadata=META)
    (line,) = [entry for entry in logs if entry["event"] == "llm.cache_hit"]
    assert line["log_level"] == "info"
    assert (line["agent"], line["step"], line["tier"], line["model"]) == (
        "analyst",
        "rate",
        "quick",
        "deepseek-flash",
    )
    assert line["saved_usd"] == pytest.approx(paid.cost_usd)
    assert len(line["key"]) == 64 and line["recorded_at"].endswith("+00:00")
    assert line["latency_ms"] == pytest.approx(250.0)
    assert not [entry for entry in logs if entry["event"] == "llm.call"]  # nothing was billed


def test_a_replay_miss_is_logged_and_names_the_call(make_gateway: MakeGateway) -> None:
    gateway = make_gateway(FakeLLM(), cache=LLMCache(), cache_mode="replay_only")
    with capture_logs() as logs, pytest.raises(CacheMiss) as raised:
        gateway.complete(ASK, metadata=META)
    (line,) = [entry for entry in logs if entry["event"] == "llm.cache_miss"]
    assert line["log_level"] == "warning"
    assert (line["agent"], line["step"], line["key"]) == ("analyst", "rate", raised.value.key)
    assert "agent='analyst', step='rate'" in str(raised.value)
    assert "the prompt or the data changed" in str(raised.value)


def test_every_hit_gets_its_own_ledger_row(tmp_lake: Lake, make_gateway: MakeGateway) -> None:
    with Database(tmp_lake) as database:
        ledger = CostLedger(database)
        gateway = make_gateway(
            FakeLLM({"analyst": "buy"}),
            cache=LLMCache(database),
            cache_mode="read_write",
            ledger=ledger,
        )
        for _ in range(3):
            gateway.complete(ASK, metadata=META)
        records = ledger.records()
    assert len({r.call_id for r in records}) == 3
    assert [r.cache_hit for r in records] == [False, True, True]
