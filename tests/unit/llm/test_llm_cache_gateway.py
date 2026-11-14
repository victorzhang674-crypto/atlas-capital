"""The gateway with the record/replay cache (Stage 25 D2 to D4), exercised with FakeLLM only."""

from collections.abc import Callable
from datetime import UTC

import pytest
from llm_helpers import OFF_PEAK
from pydantic import BaseModel

from atlas.core.config import AtlasConfig, LLMBudgets, LLMCacheConfig, LLMConfig
from atlas.core.errors import BudgetExceeded, CacheMiss, ConfigError, LLMError
from atlas.core.run_context import run_context
from atlas.core.storage import Database, Lake, get_database
from atlas.core.types.base import FrozenDict
from atlas.llm import (
    CostLedger,
    FakeLLM,
    FakeReply,
    LLMGateway,
    Message,
    ToolCall,
    Usage,
    build_gateway,
)
from atlas.llm.cache import CacheEntry, LLMCache, StoredResult, cache_key

MakeGateway = Callable[..., LLMGateway]

ASK = [Message.user("Rate AAPL.")]
META = {"agent": "analyst", "step": "rate", "prompt_version": "v1"}
ONE_DOLLAR = FakeReply(content="ok", usage=Usage(input_tokens=2_000_000, output_tokens=1_166_667))


class Verdict(BaseModel):
    action: str
    confidence: float


def agent_run(gateway: LLMGateway, messages: list[Message] = ASK, **kwargs: object):  # type: ignore[no-untyped-def]
    """One call of the scripted agent."""
    return gateway.complete(messages, metadata=META, **kwargs)  # type: ignore[arg-type]


# -- the acceptance criterion --------------------------------------------------------------


def test_the_same_scripted_agent_twice_in_read_write_makes_one_provider_call(
    make_gateway: MakeGateway,
) -> None:
    fake = FakeLLM({"analyst": "buy"})
    gateway = make_gateway(fake, cache=LLMCache(), cache_mode="read_write")
    first, second = agent_run(gateway), agent_run(gateway)
    assert len(fake.calls) == 1
    assert (first.content, second.content) == ("buy", "buy")
    assert (first.cache_hit, second.cache_hit) == (False, True)


def test_replay_only_with_a_miss_fails_loudly_and_never_calls(make_gateway: MakeGateway) -> None:
    fake = FakeLLM({"analyst": "buy"})
    gateway = make_gateway(fake, cache=LLMCache(), cache_mode="replay_only")
    with pytest.raises(CacheMiss, match="replay_only") as raised:
        agent_run(gateway)
    assert fake.calls == []
    assert isinstance(raised.value, LLMError)  # callers that fail safe on LLMError hold
    assert raised.value.key == cache_key(gateway_request(make_gateway, ASK), provider="fake")


def gateway_request(make_gateway: MakeGateway, messages: list[Message]):  # type: ignore[no-untyped-def]
    """The ProviderRequest the gateway builds for `ASK` with META (via a recording fake)."""
    fake = FakeLLM({"analyst": "x"})
    agent_run(make_gateway(fake), messages)
    return fake.calls[0]


def test_the_key_hashes_the_request_the_provider_sees(make_gateway: MakeGateway) -> None:
    fake = FakeLLM({"analyst": "buy"})
    cache = LLMCache()
    agent_run(make_gateway(fake, cache=cache, cache_mode="read_write"))
    sent = fake.calls[0]
    assert sent.messages[0].role == "system"  # the data policy is already in it
    stored = cache.get(cache_key(sent, provider="fake"))
    assert stored is not None
    import hashlib

    assert hashlib.sha256(stored.request_json.encode()).hexdigest() == stored.key


# -- modes -----------------------------------------------------------------------------------


def test_replay_only_replays_what_was_recorded(make_gateway: MakeGateway) -> None:
    cache = LLMCache()
    recorder = FakeLLM({"analyst": "buy"})
    agent_run(make_gateway(recorder, cache=cache, cache_mode="read_write"))
    fake = FakeLLM()
    replayed = agent_run(make_gateway(fake, cache=cache, cache_mode="replay_only"))
    assert (replayed.content, replayed.cache_hit, fake.calls) == ("buy", True, [])


def test_record_calls_again_and_replaces_the_stored_answer(make_gateway: MakeGateway) -> None:
    # Each recording gateway calls the provider for a key once and replaces the stored answer
    # (audit A1: later identical calls of the same gateway replay that fresh answer).
    fake = FakeLLM({"analyst": ["one", "two"]})
    cache = LLMCache()
    assert agent_run(make_gateway(fake, cache=cache, cache_mode="record")).content == "one"
    assert agent_run(make_gateway(fake, cache=cache, cache_mode="record")).content == "two"
    assert len(fake.calls) == 2 and cache.stats().entries == 1
    replay = make_gateway(FakeLLM(), cache=cache, cache_mode="replay_only")
    assert agent_run(replay).content == "two"  # the newest recording wins


def test_read_write_keeps_the_first_recording_when_two_writers_race(
    make_gateway: MakeGateway,
) -> None:
    cache = LLMCache()
    # Both callers missed, then both stored: the second put must not replace the first.
    first = make_gateway(FakeLLM({"analyst": "first"}), cache=cache, cache_mode="read_write")
    agent_run(first)
    key = cache.entries()[0].key
    late = StoredResult(content="late", usage=Usage(), cost_usd=0.0)
    entry = cache.entries()[0]
    assert (
        cache.put(
            CacheEntry.create(
                key=key,
                request_json=entry.request_json,
                provider="fake",
                model=entry.model,
                result=late,
                created_at=entry.created_at,
            )
        )
        is False
    )
    assert (
        agent_run(make_gateway(FakeLLM(), cache=cache, cache_mode="replay_only")).content == "first"
    )


@pytest.mark.parametrize("cache", [None, "given"])
def test_off_never_looks_up_or_stores(make_gateway: MakeGateway, cache: str | None) -> None:
    store = LLMCache() if cache else None
    fake = FakeLLM({"analyst": "buy"})
    gateway = make_gateway(fake, cache=store, cache_mode="off")
    agent_run(gateway), agent_run(gateway)
    assert len(fake.calls) == 2
    assert store is None or store.stats().entries == 0


def test_a_gateway_with_no_cache_never_caches_whatever_the_config_says(
    make_gateway: MakeGateway,
) -> None:
    fake = FakeLLM({"analyst": "buy"})
    gateway = make_gateway(fake)  # config default is read_write, but there is no cache
    assert gateway.cache is None and gateway.cache_mode == "off"
    agent_run(gateway), agent_run(gateway)
    assert len(fake.calls) == 2


@pytest.mark.parametrize("mode", ["read_write", "replay_only", "record"])
def test_a_mode_that_needs_a_cache_without_one_is_a_config_error(
    make_gateway: MakeGateway, mode: str
) -> None:
    with pytest.raises(ConfigError, match="cache"):
        make_gateway(FakeLLM(), cache_mode=mode)


def test_the_configured_mode_applies_when_a_cache_is_given(make_gateway: MakeGateway) -> None:
    config = AtlasConfig(llm=LLMConfig(cache=LLMCacheConfig(mode="replay_only")))
    gateway = make_gateway(FakeLLM(), config, cache=LLMCache())
    assert gateway.cache_mode == "replay_only"
    with pytest.raises(CacheMiss):
        agent_run(gateway)
    assert make_gateway(FakeLLM(), config, cache=LLMCache(), cache_mode="record").cache_mode == (
        "record"
    )  # an explicit mode beats the config


# -- what is and is not a hit ----------------------------------------------------------------


def test_a_prompt_version_bump_is_a_miss(make_gateway: MakeGateway) -> None:
    fake = FakeLLM({"analyst": "buy"})
    gateway = make_gateway(fake, cache=LLMCache(), cache_mode="read_write")
    agent_run(gateway)
    gateway.complete(ASK, metadata={**META, "prompt_version": "v2"})
    assert len(fake.calls) == 2


def test_changed_data_and_another_tier_are_misses(make_gateway: MakeGateway) -> None:
    fake = FakeLLM({"analyst": "buy"})
    gateway = make_gateway(fake, cache=LLMCache(), cache_mode="read_write")
    agent_run(gateway)
    agent_run(gateway, [Message.user("Rate MSFT.")])
    agent_run(gateway, tier="deep")
    agent_run(gateway, temperature=0.5)
    assert len(fake.calls) == 4
    agent_run(gateway, tier="deep")  # and each of them is now recorded
    assert len(fake.calls) == 4


def test_two_agents_asking_the_same_question_share_an_answer(make_gateway: MakeGateway) -> None:
    fake = FakeLLM({"analyst": "buy", "pm": "sell"})
    gateway = make_gateway(fake, cache=LLMCache(), cache_mode="read_write")
    gateway.complete(ASK, metadata={"agent": "analyst", "prompt_version": "v1"})
    shared = gateway.complete(ASK, metadata={"agent": "pm", "prompt_version": "v1"})
    assert (shared.content, shared.cache_hit, len(fake.calls)) == ("buy", True, 1)


def test_a_hit_is_free_and_says_so(make_gateway: MakeGateway) -> None:
    gateway = make_gateway(
        FakeLLM({"analyst": ONE_DOLLAR}), cache=LLMCache(), cache_mode="read_write"
    )
    first, hit = agent_run(gateway), agent_run(gateway)
    assert first.cost_usd == pytest.approx(1.0)
    assert hit.cost_usd == 0.0 and hit.usage == Usage()
    assert (hit.attempts, hit.model, hit.finish_reason) == (0, "deepseek-flash", "stop")
    assert hit.latency_ms == pytest.approx(250.0)  # two timer readings


def test_a_hit_writes_a_cache_hit_row_with_cost_zero(make_gateway: MakeGateway) -> None:
    ledger = CostLedger()
    gateway = make_gateway(
        FakeLLM({"analyst": ONE_DOLLAR}), cache=LLMCache(), cache_mode="read_write", ledger=ledger
    )
    with run_context(config=AtlasConfig(), stage="test") as run:
        agent_run(gateway)
        agent_run(gateway)
    paid, hit = ledger.records()
    assert (paid.cache_hit, hit.cache_hit) == (False, True)
    assert hit.cost_usd == 0.0
    assert (hit.input_tokens, hit.output_tokens, hit.cached_input_tokens) == (0, 0, 0)
    assert (hit.agent, hit.step, hit.tier, hit.model) == (
        "analyst",
        "rate",
        "quick",
        "deepseek-flash",
    )
    assert (hit.provider, hit.run_id, hit.status, hit.attempt) == ("fake", run.run_id, "ok", 1)
    assert hit.ts == OFF_PEAK.astimezone(UTC) and hit.day == OFF_PEAK.date()
    assert ledger.spent_in_run(run.run_id) == pytest.approx(1.0)  # the hit added nothing
    assert ledger.cache_counts() == (1, 1)


def test_a_hit_spends_no_budget_but_a_miss_still_does(make_gateway: MakeGateway) -> None:
    cache = LLMCache()
    agent_run(make_gateway(FakeLLM({"analyst": ONE_DOLLAR}), cache=cache, cache_mode="record"))
    broke = AtlasConfig(llm=LLMConfig(budgets=LLMBudgets(daily_usd=0.0001)))
    fake = FakeLLM({"analyst": "x"})
    gateway = make_gateway(fake, broke, cache=cache, cache_mode="read_write")
    assert agent_run(gateway).cache_hit is True  # nothing is spent, so nothing is refused
    with pytest.raises(BudgetExceeded):
        gateway.complete([Message.user("something new")], metadata=META)
    assert fake.calls == []


# -- structured output and tools --------------------------------------------------------------


def test_a_structured_hit_returns_the_parsed_model(make_gateway: MakeGateway) -> None:
    fake = FakeLLM({"analyst": Verdict(action="buy", confidence=0.8)})
    gateway = make_gateway(fake, cache=LLMCache(), cache_mode="read_write")
    first, hit = agent_run(gateway, schema=Verdict), agent_run(gateway, schema=Verdict)
    assert len(fake.calls) == 1
    assert hit.cache_hit and hit.parsed == first.parsed == Verdict(action="buy", confidence=0.8)


def test_a_repair_retry_is_stored_as_one_answer(make_gateway: MakeGateway) -> None:
    good = Verdict(action="hold", confidence=0.5)
    fake = FakeLLM({"analyst": ["not json", good]})
    cache = LLMCache()
    gateway = make_gateway(fake, cache=cache, cache_mode="read_write")
    first = agent_run(gateway, schema=Verdict)
    assert first.attempts == 2 and len(fake.calls) == 2
    stored = cache.entries()[0].result()
    assert (stored.attempts, stored.cost_usd) == (2, pytest.approx(first.cost_usd))
    assert stored.usage == first.usage
    hit = agent_run(gateway, schema=Verdict)
    assert hit.parsed == good and len(fake.calls) == 2


def test_tool_calls_survive_a_replay(make_gateway: MakeGateway) -> None:
    call = ToolCall(id="c1", name="lookup", arguments=FrozenDict({"symbol": "AAPL", "n": [1, 2]}))
    fake = FakeLLM({"analyst": FakeReply(tool_calls=(call,))})
    gateway = make_gateway(fake, cache=LLMCache(), cache_mode="read_write")
    agent_run(gateway)
    hit = agent_run(gateway)
    assert hit.cache_hit and hit.tool_calls == (call,) and hit.finish_reason == "tool_calls"


def test_failures_are_never_stored(make_gateway: MakeGateway) -> None:
    cache = LLMCache()
    failing = make_gateway(FakeLLM({"analyst": LLMError("boom")}), cache=cache, cache_mode="record")
    with pytest.raises(LLMError, match="boom"):
        agent_run(failing)
    invalid = make_gateway(FakeLLM({"analyst": "nope"}), cache=cache, cache_mode="record")
    with pytest.raises(LLMError, match="schema"):
        agent_run(invalid, schema=Verdict)
    assert cache.stats().entries == 0


def test_a_corrupt_entry_fails_safe(make_gateway: MakeGateway) -> None:
    cache = LLMCache()
    fake = FakeLLM({"analyst": "buy"})
    gateway = make_gateway(fake, cache=cache, cache_mode="read_write")
    agent_run(gateway)
    good = cache.entries()[0]
    cache.put(CacheEntry(**{**good.__dict__, "response_json": "{nope"}), replace=True)
    with pytest.raises(LLMError, match="corrupt"):
        agent_run(gateway)
    assert len(fake.calls) == 1  # no silent fall-through to the provider


def test_a_stored_answer_that_no_longer_fits_the_schema_fails_safe(
    make_gateway: MakeGateway,
) -> None:
    cache = LLMCache()
    gateway = make_gateway(
        FakeLLM({"analyst": Verdict(action="buy", confidence=1.0)}),
        cache=cache,
        cache_mode="read_write",
    )
    agent_run(gateway, schema=Verdict)
    good = cache.entries()[0]
    bad = StoredResult(content="not json", usage=Usage(), cost_usd=0.0)
    cache.put(
        CacheEntry.create(
            key=good.key,
            request_json=good.request_json,
            provider=good.provider,
            model=good.model,
            result=bad,
            created_at=good.created_at,
        ),
        replace=True,
    )
    with pytest.raises(LLMError, match="does not match schema Verdict"):
        agent_run(gateway, schema=Verdict)


# -- persistence and wiring -------------------------------------------------------------------


def test_a_recording_in_duckdb_replays_in_a_later_session(
    tmp_lake: Lake, make_gateway: MakeGateway
) -> None:
    with Database(tmp_lake) as first:
        agent_run(
            make_gateway(
                FakeLLM({"analyst": Verdict(action="buy", confidence=0.9)}),
                cache=LLMCache(first),
                cache_mode="read_write",
            ),
            schema=Verdict,
        )
    fake = FakeLLM()
    with Database(tmp_lake) as second:
        replayed = agent_run(
            make_gateway(fake, cache=LLMCache(second), cache_mode="replay_only"), schema=Verdict
        )
    assert replayed.cache_hit and replayed.parsed == Verdict(action="buy", confidence=0.9)
    assert fake.calls == []


def test_build_gateway_wires_a_database_cache_with_a_lake(tmp_lake: Lake) -> None:
    from pydantic import SecretStr

    from atlas.core.config import Secrets

    config = AtlasConfig(secrets=Secrets(deepseek_api_key=SecretStr("sk-test-not-real")))
    gateway = build_gateway(config, lake=tmp_lake)
    assert gateway.cache_mode == "read_write" and gateway.cache is not None
    cache_entry = CacheEntry.create(
        key="k",
        request_json="{}",
        provider="openai_compatible",
        model="deepseek-flash",
        result=StoredResult(content="x", usage=Usage(), cost_usd=0.0),
        created_at=OFF_PEAK,
    )
    gateway.cache.put(cache_entry)
    database = get_database(tmp_lake)  # the entry is in the lake's llm_cache table
    assert database.query("select key from llm_cache")["key"].tolist() == ["k"]
    database.close()


def test_build_gateway_without_a_lake_caches_in_memory_and_honours_off() -> None:
    from pydantic import SecretStr

    from atlas.core.config import Secrets

    secrets = Secrets(deepseek_api_key=SecretStr("sk-test-not-real"))
    assert build_gateway(AtlasConfig(secrets=secrets)).cache_mode == "read_write"
    off = AtlasConfig(secrets=secrets, llm=LLMConfig(cache=LLMCacheConfig(mode="off")))
    gateway = build_gateway(off)
    assert gateway.cache_mode == "off" and gateway.cache is None
