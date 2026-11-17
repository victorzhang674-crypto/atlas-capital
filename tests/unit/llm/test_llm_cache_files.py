"""Phase 3 checkpoint (Stage 25 follow-up): recordings travel as files.

`write_export` / `read_export` turn cache entries into JSON lines and back, and
`LLMCache.load` stores them, so a live run recorded once (Stages 29-36: "a recorded live
fixture", "runs offline from the cache") can be committed as a fixture and replayed by a test
or by CI with no API key.
"""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from atlas.core.config import AtlasConfig
from atlas.core.errors import CacheMiss, LLMError
from atlas.core.storage import Database, Lake
from atlas.llm import FakeLLM, FakeReply, LLMGateway, Message, OfflineProvider, ToolCall
from atlas.llm.cache import (
    EXPORT_FIELDS,
    LLMCache,
    entry_from_record,
    export_record,
    read_export,
    write_export,
)

META = {"agent": "analyst", "step": "rate", "prompt_version": "v1"}


class Verdict(BaseModel):
    action: str


def ask(symbol: str) -> list[Message]:
    return [Message.user(f"Rate {symbol}. Ünïcode stays as it is.")]


def recorded(cache: LLMCache | None = None) -> tuple[LLMCache, FakeLLM]:
    """A cache with three real recordings: plain text, a schema answer and a tool call."""
    cache = cache if cache is not None else LLMCache()
    call = ToolCall(id="c1", name="news", arguments={"symbol": "CCC"})
    fake = FakeLLM(
        {("analyst", "rate"): ["buy AAA", Verdict(action="sell"), FakeReply(tool_calls=(call,))]}
    )
    gateway = LLMGateway(fake, AtlasConfig(), cache=cache, cache_mode="read_write")
    gateway.complete(ask("AAA"), metadata=META)
    gateway.complete(ask("BBB"), metadata=META, schema=Verdict)
    gateway.complete(ask("CCC"), metadata=META)
    return cache, fake


def test_a_file_round_trip_keeps_every_entry_exactly(tmp_path: Path) -> None:
    cache, _ = recorded()
    path = tmp_path / "fixtures" / "run.jsonl"
    assert write_export(cache.entries(), path) == 3
    assert read_export(path) == cache.entries()
    first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert tuple(sorted(first)) == tuple(sorted(EXPORT_FIELDS))
    assert "Ünïcode" in path.read_text(encoding="utf-8")  # readable, not \u-escaped


def test_an_export_is_byte_stable(tmp_path: Path) -> None:
    cache, _ = recorded()
    one, two = tmp_path / "one.jsonl", tmp_path / "two.jsonl"
    write_export(cache.entries(), one)
    write_export(read_export(one), two)
    assert one.read_bytes() == two.read_bytes()  # a re-export of a fixture shows no diff


def test_a_loaded_file_replays_with_no_provider(tmp_path: Path) -> None:
    cache, fake = recorded()
    path = tmp_path / "run.jsonl"
    write_export(cache.entries(), path)
    fresh = LLMCache()
    assert fresh.load(read_export(path)) == 3
    offline = LLMGateway(
        OfflineProvider("fake"), AtlasConfig(), cache=fresh, cache_mode="replay_only"
    )
    plain = offline.complete(ask("AAA"), metadata=META)
    parsed = offline.complete(ask("BBB"), metadata=META, schema=Verdict)
    tools = offline.complete(ask("CCC"), metadata=META)
    assert plain.content == "buy AAA" and plain.cache_hit and plain.cost_usd == 0.0
    assert parsed.parsed == Verdict(action="sell")
    assert tools.tool_calls[0].name == "news" and tools.finish_reason == "tool_calls"
    with pytest.raises(CacheMiss):
        offline.complete(ask("DDD"), metadata=META)
    assert len(fake.calls) == 3  # the recording session's calls; the replay made none


def test_load_keeps_what_is_stored_unless_told_to_replace() -> None:
    cache, _ = recorded()
    entries = cache.entries()
    other = LLMCache()
    assert other.load(entries[:2]) == 2
    assert other.load(entries) == 1  # only the new one
    assert other.load(entries, replace=True) == 3
    assert other.entries() == entries and other.load(()) == 0


def test_a_duckdb_cache_loads_a_file_and_compressed_bodies_survive(tmp_path: Path) -> None:
    source, _ = recorded()
    path = tmp_path / "run.jsonl"
    write_export(source.entries(), path)
    with Database(Lake(tmp_path / "lake")) as database:
        stored = LLMCache(database, compress_min_bytes=1)
        assert stored.load(()) == 0
        assert stored.load(read_export(path)) == 3
        assert stored.load(read_export(path)) == 0
        assert stored.entries() == source.entries()
        again = tmp_path / "again.jsonl"
        write_export(stored.entries(), again)
    assert again.read_bytes() == path.read_bytes()


def _lines(tmp_path: Path) -> tuple[Path, list[dict[str, object]]]:
    cache, _ = recorded()
    path = tmp_path / "run.jsonl"
    write_export(cache.entries(), path)
    return path, [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _rewrite(path: Path, records: list[object]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def test_an_edited_request_is_refused(tmp_path: Path) -> None:
    path, records = _lines(tmp_path)
    records[1]["request"]["messages"][-1]["content"] = "Rate BBB, but nicer."  # type: ignore[index]
    _rewrite(path, records)
    with pytest.raises(LLMError, match=r"run\.jsonl, line 2: .*not the SHA-256 of the request"):
        read_export(path)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda r: r.pop("usage"), "needs exactly the fields"),
        (lambda r: r.update(extra=1), "needs exactly the fields"),
        (lambda r: r.update(created_at="2026-11-17T10:00:00"), "has no timezone"),
        (lambda r: r.update(created_at="yesterday"), "is not a time"),
        (lambda r: r.update(created_at=5), "is not a time"),
        (lambda r: r.update(model=""), "model must be a non-empty string"),
        (lambda r: r.update(provider=None), "provider must be a non-empty string"),
        (lambda r: r.update(response={"content": 5}), "corrupt LLM cache entry"),
        (lambda r: r.update(usage={"cost_usd": float("nan")}), "not canonical JSON"),
        (lambda r: r.update(usage={"usage": {}, "cost_usd": -1}), "corrupt LLM cache entry"),
    ],
)
def test_a_damaged_line_is_refused(tmp_path: Path, change, message: str) -> None:  # type: ignore[no-untyped-def]
    path, records = _lines(tmp_path)
    change(records[2])
    _rewrite(path, records)
    with pytest.raises(LLMError, match=rf"line 3: .*{message}"):
        read_export(path)


def test_lines_that_are_not_json_objects_are_refused(tmp_path: Path) -> None:
    path, records = _lines(tmp_path)
    path.write_text(json.dumps(records[0]) + "\n{not json\n", encoding="utf-8")
    with pytest.raises(LLMError, match="line 2: not JSON"):
        read_export(path)
    path.write_text('["a", "list"]\n', encoding="utf-8")
    with pytest.raises(LLMError, match=r"line 1: .*needs exactly the fields.*list"):
        read_export(path)


def test_a_repeated_key_is_refused_and_blank_lines_are_skipped(tmp_path: Path) -> None:
    path, records = _lines(tmp_path)
    _rewrite(path, [records[0], records[1], records[0]])
    with pytest.raises(LLMError, match=r"line 3: key \w{64} repeats line 1"):
        read_export(path)
    path.write_text(
        "\n" + json.dumps(records[0]) + "\n\n   \n" + json.dumps(records[1]) + "\n",
        encoding="utf-8",
    )
    assert len(read_export(path)) == 2


def test_export_record_and_entry_from_record_are_inverses() -> None:
    cache, _ = recorded()
    for entry in cache.entries():
        record = export_record(entry)
        assert json.loads(json.dumps(record)) == record  # plain JSON
        assert entry_from_record(record) == entry
    assert read_export.__doc__ and write_export.__doc__
