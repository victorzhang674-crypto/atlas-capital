"""`LLMCache`: the store behind the gateway, in memory and in DuckDB (Stage 25 D5, D6)."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from atlas.core.errors import LLMError
from atlas.core.storage import Database, Lake
from atlas.core.types.base import FrozenDict
from atlas.llm import ToolCall, Usage
from atlas.llm.cache import CacheEntry, LLMCache, StoredResult

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def result(content: str = "hello", **overrides: object) -> StoredResult:
    values: dict[str, object] = {
        "content": content,
        "tool_calls": (),
        "finish_reason": "stop",
        "usage": Usage(input_tokens=10, output_tokens=5, reasoning_tokens=2),
        "cost_usd": 0.0012,
        "attempts": 1,
    }
    values.update(overrides)
    return StoredResult.model_validate(values)


def entry(
    key: str = "k1", *, model: str = "deepseek-flash", at: datetime = T0, **kw: object
) -> CacheEntry:
    return CacheEntry.create(
        key=key,
        request_json='{"messages":[]}',
        provider="fake",
        model=model,
        result=kw.pop("result", result()),  # type: ignore[arg-type]
        created_at=at,
    )


@pytest.fixture(params=["memory", "duckdb"])
def cache(request: pytest.FixtureRequest, tmp_lake: Lake) -> Iterator[LLMCache]:
    if request.param == "memory":
        yield LLMCache()
        return
    database = Database(tmp_lake)
    yield LLMCache(database)
    database.close()


def test_get_of_an_unknown_key_is_none(cache: LLMCache) -> None:
    assert cache.get("nope") is None


def test_round_trip_keeps_every_field(cache: LLMCache) -> None:
    call = ToolCall(id="c1", name="lookup", arguments=FrozenDict({"a": 1, "b": [1, 2]}))
    stored = result("text", tool_calls=(call,), finish_reason="tool_calls", attempts=2)
    assert cache.put(entry(result=stored)) is True
    got = cache.get("k1")
    assert got is not None
    assert (got.key, got.created_at, got.provider, got.model) == (
        "k1",
        T0,
        "fake",
        "deepseek-flash",
    )
    assert got.request_json == '{"messages":[]}'
    assert got.result() == stored
    assert got.result().usage == Usage(input_tokens=10, output_tokens=5, reasoning_tokens=2)
    assert got.result().cost_usd == 0.0012


def test_first_writer_wins_unless_replacing(cache: LLMCache) -> None:
    assert cache.put(entry(result=result("first"))) is True
    assert cache.put(entry(result=result("second"))) is False
    got = cache.get("k1")
    assert got is not None and got.result().content == "first"
    assert cache.put(entry(result=result("third")), replace=True) is True
    got = cache.get("k1")
    assert got is not None and got.result().content == "third"
    assert cache.stats().entries == 1


def test_entries_are_listed_oldest_first(cache: LLMCache) -> None:
    cache.put(entry("b", at=T0 + timedelta(hours=1)))
    cache.put(entry("a", at=T0))
    cache.put(entry("c", at=T0 + timedelta(hours=1)))
    assert [e.key for e in cache.entries()] == ["a", "b", "c"]


def test_stats(cache: LLMCache) -> None:
    empty = cache.stats()
    assert (empty.entries, empty.bytes, empty.oldest, empty.newest) == (0, 0, None, None)
    assert dict(empty.models) == {}
    cache.put(entry("a", model="deepseek-flash", at=T0))
    cache.put(entry("b", model="deepseek-flash", at=T0 + timedelta(days=2)))
    cache.put(entry("c", model="deepseek-v4-pro", at=T0 + timedelta(days=1)))
    stats = cache.stats()
    assert stats.entries == 3
    assert dict(stats.models) == {"deepseek-flash": 2, "deepseek-v4-pro": 1}
    assert (stats.oldest, stats.newest) == (T0, T0 + timedelta(days=2))
    one = entry("a")
    assert stats.bytes == 3 * (
        len(one.request_json.encode())
        + len(one.response_json.encode())
        + len(one.usage_json.encode())
    )


def test_prune_removes_strictly_older_entries(cache: LLMCache) -> None:
    cache.put(entry("old", at=T0 - timedelta(days=40)))
    cache.put(entry("edge", at=T0))
    cache.put(entry("new", at=T0 + timedelta(days=1)))
    assert cache.prune(T0) == 1
    assert [e.key for e in cache.entries()] == ["edge", "new"]
    assert cache.prune(T0 - timedelta(days=99)) == 0


def test_a_corrupt_entry_raises_llm_error_not_a_guess() -> None:
    bad = CacheEntry(
        key="k",
        created_at=T0,
        provider="fake",
        model="m",
        request_json="{}",
        response_json="not json",
        usage_json="{}",
    )
    with pytest.raises(LLMError, match="corrupt"):
        bad.result()


def test_memory_cache_is_shared_between_threads() -> None:
    from concurrent.futures import ThreadPoolExecutor

    cache = LLMCache()
    with ThreadPoolExecutor(8) as pool:
        wins = list(pool.map(lambda i: cache.put(entry("same", result=result(str(i)))), range(32)))
    assert wins.count(True) == 1 and cache.stats().entries == 1


def test_prune_rejects_a_naive_cutoff(cache: LLMCache) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        cache.prune(datetime(2026, 11, 1))


def test_answer_and_meter_go_to_their_own_columns() -> None:  # audit (mutation survivors)
    import json

    e = entry()
    assert set(json.loads(e.response_json)) == {"content", "tool_calls", "finish_reason"}
    assert set(json.loads(e.usage_json)) == {"usage", "cost_usd", "attempts"}
    assert json.loads(e.usage_json)["usage"]["input_tokens"] == 10


def test_stored_timestamps_come_back_as_plain_aware_datetimes(cache: LLMCache) -> None:
    cache.put(entry())
    got = cache.get("k1")
    assert got is not None and type(got.created_at) is datetime
    assert got.created_at.utcoffset() == timedelta(0)
    assert all(type(e.created_at) is datetime for e in cache.entries())
