"""Stage 25 backlog follow-ups (2026-11-14): F1 concurrent writers, F2 endpoint in the key,
F3 compressed bodies."""

from collections.abc import Callable, Iterator

import pytest
from pydantic import SecretStr

from atlas.core.config import AtlasConfig, LLMCacheConfig, LLMConfig, Secrets
from atlas.core.errors import LLMError
from atlas.core.storage import Database, Lake, get_database
from atlas.llm import (
    FakeLLM,
    LLMGateway,
    Message,
    ProviderRequest,
    ProviderResponse,
    Usage,
    build_gateway,
)
from atlas.llm.cache import CacheEntry, LLMCache, StoredResult, cache_key
from atlas.llm.providers.openai_compat import OpenAICompatibleProvider

MakeGateway = Callable[..., LLMGateway]
ASK = [Message.user("Rate AAPL.")]
META = {"agent": "analyst", "step": "rate", "prompt_version": "v1"}


class Interleaving:
    """A provider that, while answering, lets another gateway record the same request first:
    the situation of two gateways sharing one cache."""

    name = "fake"

    def __init__(self, answer: str, meanwhile: Callable[[], object]) -> None:
        self.answer, self.meanwhile, self.calls = answer, meanwhile, 0

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        self.meanwhile()
        return ProviderResponse(
            content=self.answer, usage=Usage(input_tokens=5), model=request.model
        )


@pytest.fixture(params=["memory", "duckdb"])
def shared(request: pytest.FixtureRequest, tmp_lake: Lake) -> Iterator[LLMCache]:
    if request.param == "memory":
        yield LLMCache()
        return
    database = Database(tmp_lake)
    yield LLMCache(database)
    database.close()


# F1: a gateway whose answer lost the race to another writer adopts the stored answer.


@pytest.mark.parametrize("mode", ["read_write", "record"])
def test_the_losing_writer_adopts_the_stored_answer(
    make_gateway: MakeGateway, shared: LLMCache, mode: str
) -> None:
    other = make_gateway(FakeLLM({"analyst": "theirs"}), cache=shared, cache_mode=mode)
    slow = Interleaving("mine", lambda: other.complete(ASK, metadata=META))
    gateway = make_gateway(slow, cache=shared, cache_mode=mode)
    result = gateway.complete(ASK, metadata=META)
    assert slow.calls == 1
    assert result.content == "theirs"  # what the cache holds, so a replay agrees
    assert (result.cache_hit, result.attempts) == (False, 1)
    assert result.cost_usd > 0  # the call was paid all the same
    assert [e.result().content for e in shared.entries()] == ["theirs"]
    replay = make_gateway(FakeLLM(), cache=shared, cache_mode="replay_only")
    assert replay.complete(ASK, metadata=META).content == "theirs"


def test_record_still_replaces_a_recording_from_before(
    make_gateway: MakeGateway, shared: LLMCache
) -> None:
    make_gateway(FakeLLM({"analyst": "old"}), cache=shared, cache_mode="read_write").complete(
        ASK, metadata=META
    )
    fresh = make_gateway(FakeLLM({"analyst": "new"}), cache=shared, cache_mode="record")
    assert fresh.complete(ASK, metadata=META).content == "new"
    assert [e.result().content for e in shared.entries()] == ["new"]


def test_swap_is_compare_and_set(shared: LLMCache) -> None:
    from datetime import UTC, datetime

    def entry(content: str, minute: int) -> CacheEntry:
        return CacheEntry.create(
            key="k",
            request_json="{}",
            provider="fake",
            model="m",
            result=StoredResult(content=content, usage=Usage(), cost_usd=0.0),
            created_at=datetime(2026, 11, 14, 12, minute, tzinfo=UTC),
        )

    first, second, third = entry("a", 0), entry("b", 1), entry("c", 2)
    assert shared.swap(first, expected=None) is True
    assert shared.swap(second, expected=None) is False  # something is stored already
    assert shared.swap(second, expected=third) is False  # not what is stored
    assert shared.swap(second, expected=first) is True
    got = shared.get("k")
    assert got is not None and got.result().content == "b"


# F2: the provider's endpoint is part of the key.


def test_the_endpoint_changes_the_key() -> None:
    from dataclasses import replace

    request = ProviderRequest(
        model="deepseek-flash",
        tier="quick",
        messages=tuple(ASK),
        max_tokens=10,
        timeout_seconds=1.0,
    )
    a = cache_key(request, provider="openai_compatible", endpoint="https://api.deepseek.com")
    b = cache_key(request, provider="openai_compatible", endpoint="https://other.example/v1")
    assert a != b
    assert (
        cache_key(
            replace(request), provider="openai_compatible", endpoint="https://api.deepseek.com"
        )
        == a
    )


def test_the_openai_compatible_endpoint_is_its_base_url() -> None:
    key = SecretStr("sk-test-not-real")
    assert OpenAICompatibleProvider("https://api.deepseek.com/", key).endpoint == (
        "https://api.deepseek.com"
    )


def test_the_gateway_keys_by_the_providers_endpoint(make_gateway: MakeGateway) -> None:
    class AtEndpoint(FakeLLM):
        endpoint = "https://one.example"

    fake = AtEndpoint({"analyst": "buy"})
    cache = LLMCache()
    make_gateway(fake, cache=cache, cache_mode="read_write").complete(ASK, metadata=META)
    sent = fake.calls[0]
    assert cache.get(cache_key(sent, provider="fake", endpoint="https://one.example")) is not None
    assert cache.get(cache_key(sent, provider="fake")) is None
    other = AtEndpoint({"analyst": "sell"})
    other.endpoint = "https://two.example"
    result = make_gateway(other, cache=cache, cache_mode="read_write").complete(ASK, metadata=META)
    assert (result.content, result.cache_hit) == ("sell", False)  # another vendor: a miss


# F3: large bodies are stored compressed.


def big_entry(key: str = "k", size: int = 20_000) -> CacheEntry:
    from datetime import UTC, datetime

    return CacheEntry.create(
        key=key,
        request_json='{"messages":"' + "news text " * (size // 10) + '"}',
        provider="fake",
        model="m",
        result=StoredResult(content="answer " * (size // 7), usage=Usage(), cost_usd=0.0),
        created_at=datetime(2026, 11, 14, 12, 0, tzinfo=UTC),
    )


def test_large_bodies_are_compressed_in_duckdb_and_round_trip(tmp_lake: Lake) -> None:
    with Database(tmp_lake) as database:
        cache = LLMCache(database, compress_min_bytes=4096)
        entry, small = big_entry(), big_entry("small", size=100)
        cache.put(entry)
        cache.put(small)
        raw = database.query("SELECT key, request_json, response_json FROM llm_cache ORDER BY key")
        stored = dict(
            zip(
                raw["key"], zip(raw["request_json"], raw["response_json"], strict=True), strict=True
            )
        )
        assert all(text.startswith("zlib:") for text in stored["k"])
        assert sum(map(len, stored["k"])) * 10 < len(entry.request_json) + len(entry.response_json)
        assert not any(text.startswith("zlib:") for text in stored["small"])  # under the threshold
        assert cache.get("k") == entry and cache.get("small") == small
        assert cache.entries() == (entry, small)
        assert cache.stats().bytes < len(entry.request_json)  # stats count stored bytes
        assert LLMCache(database).get("k") == entry  # any reader unpacks, whatever its setting


def test_a_corrupt_compressed_body_fails_safe(tmp_lake: Lake) -> None:
    with Database(tmp_lake) as database:
        row = {
            "key": "k",
            "created_at": big_entry().created_at,
            "provider": "fake",
            "model": "m",
            "request_json": "zlib:not-base64!",
            "response_json": "{}",
            "usage": "{}",
        }
        database.put_llm_cache([row], replace=False)
        with pytest.raises(LLMError, match="corrupt"):
            LLMCache(database).get("k")


def test_compression_threshold_is_configured_and_wired(tmp_lake: Lake) -> None:
    assert AtlasConfig().llm.cache.compress_min_bytes == 4096
    with pytest.raises(ValueError):
        LLMCacheConfig(compress_min_bytes=0)
    config = AtlasConfig(
        secrets=Secrets(deepseek_api_key=SecretStr("sk-test-not-real")),
        llm=LLMConfig(cache=LLMCacheConfig(compress_min_bytes=1000)),
    )
    gateway = build_gateway(config, lake=tmp_lake)
    assert gateway.cache is not None and gateway.cache.compress_min_bytes == 1000
    off = LLMCacheConfig(compress_min_bytes=None)
    assert off.compress_min_bytes is None
    get_database(tmp_lake).close()


@pytest.mark.parametrize("body", ["{nope", "zlib:not-base64!"])
def test_record_replaces_an_unreadable_recording(
    tmp_lake: Lake, make_gateway: MakeGateway, body: str
) -> None:
    with Database(tmp_lake) as database:
        cache = LLMCache(database)
        fake = FakeLLM({"analyst": "fresh"})
        make_gateway(FakeLLM({"analyst": "old"}), cache=cache, cache_mode="read_write").complete(
            ASK, metadata=META
        )
        key = cache.entries()[0].key
        row = database.query("SELECT * FROM llm_cache").iloc[0].to_dict()
        row["created_at"] = row["created_at"].to_pydatetime()
        row["response_json"] = body
        database.put_llm_cache([row], replace=True)
        recorder = make_gateway(fake, cache=cache, cache_mode="record")
        assert recorder.complete(ASK, metadata=META).content == "fresh"
        got = cache.get(key)
        assert got is not None and got.result().content == "fresh"
        assert cache.delete(key) is True and cache.delete(key) is False


def test_memory_delete() -> None:
    cache = LLMCache()
    cache.put(big_entry())
    assert cache.delete("k") is True and cache.get("k") is None and cache.delete("k") is False


def test_a_loser_keeps_its_answer_when_the_winner_is_gone(make_gateway: MakeGateway) -> None:
    class PrunedInTheWindow(LLMCache):
        """Another writer won the compare-and-set, then the entry was pruned before the
        loser read it back (e.g. `atlas llm cache prune` ran in between)."""

        def swap(self, entry: CacheEntry, *, expected: CacheEntry | None) -> bool:
            return False

    gateway = make_gateway(
        FakeLLM({"analyst": "mine"}), cache=PrunedInTheWindow(), cache_mode="read_write"
    )
    result = gateway.complete(ASK, metadata=META)
    assert (result.content, result.cache_hit) == ("mine", False)


# Mutation survivors of the follow-up (run 1): pinned here.


def test_the_stored_request_is_the_preimage_of_the_endpoint_key(make_gateway: MakeGateway) -> None:
    import hashlib

    class AtEndpoint(FakeLLM):
        endpoint = "https://one.example"

    cache = LLMCache()
    make_gateway(AtEndpoint({"analyst": "buy"}), cache=cache, cache_mode="read_write").complete(
        ASK, metadata=META
    )
    (entry,) = cache.entries()
    assert hashlib.sha256(entry.request_json.encode()).hexdigest() == entry.key
    assert '"endpoint":"https://one.example"' in entry.request_json


def test_a_body_of_exactly_the_threshold_is_compressed(tmp_lake: Lake) -> None:
    entry = big_entry(size=100)
    size = len(entry.response_json.encode())
    with Database(tmp_lake) as database:
        LLMCache(database, compress_min_bytes=size).put(entry)
        LLMCache(database, compress_min_bytes=size + 1).put(big_entry("below", size=100))
        raw = database.query("SELECT key, response_json FROM llm_cache ORDER BY key")
    assert dict(
        zip(raw["key"], [t.startswith("zlib:") for t in raw["response_json"]], strict=True)
    ) == {
        "below": False,
        "k": True,
    }


def test_a_compressed_body_with_stray_characters_is_corrupt(tmp_lake: Lake) -> None:
    with Database(tmp_lake) as database:
        LLMCache(database, compress_min_bytes=10).put(big_entry())
        row = database.query("SELECT * FROM llm_cache").iloc[0].to_dict()
        row["created_at"] = row["created_at"].to_pydatetime()
        body = str(row["response_json"])
        row["response_json"] = body[:20] + "!" + body[20:]  # a lenient decoder would skip it
        database.put_llm_cache([row], replace=True)
        with pytest.raises(LLMError, match="corrupt LLM cache entry k: unreadable compressed"):
            LLMCache(database).get("k")


def test_a_losing_writer_adopts_a_structured_answer(make_gateway: MakeGateway) -> None:
    from pydantic import BaseModel
    from structlog.testing import capture_logs

    class Verdict(BaseModel):
        action: str

    cache = LLMCache()
    other = make_gateway(
        FakeLLM({"analyst": Verdict(action="sell")}), cache=cache, cache_mode="read_write"
    )
    slow = Interleaving(
        '{"action": "buy"}', lambda: other.complete(ASK, metadata=META, schema=Verdict)
    )
    gateway = make_gateway(slow, cache=cache, cache_mode="read_write")
    with capture_logs() as logs:
        result = gateway.complete(ASK, metadata=META, schema=Verdict)
    assert result.parsed == Verdict(action="sell")
    (line,) = [e for e in logs if e["event"] == "llm.cache_adopted"]
    assert line["log_level"] == "info"
    assert (line["agent"], line["step"]) == ("analyst", "rate")
    assert line["key"] == cache.entries()[0].key and line["recorded_at"].endswith("+00:00")
