"""Stage 25: `atlas llm cache stats|export|prune`."""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from atlas.cli.app import app
from atlas.cli.llm_cmd import parse_older_than
from atlas.core.clock import LiveClock
from atlas.core.storage import Database, Lake
from atlas.llm import CostLedger, Usage
from atlas.llm.cache import CacheEntry, LLMCache, StoredResult
from atlas.llm.cost import UsageRecord

BASE_ENV = {"COLUMNS": "200", "NO_COLOR": "1"}
NOW = LiveClock().now()  # fixtures are aged relative to the CLI's own wall clock


def _plain(text: str) -> str:
    """Output without colour codes and without the table's box-drawing characters."""
    return re.sub(r"[│┌┐└┘├┤┬┴┼─]", " ", re.sub(r"\x1b\[[0-9;]*m", "", text))


def _entry(key: str, age_days: float, model: str = "deepseek-flash") -> CacheEntry:
    return CacheEntry.create(
        key=key,
        request_json=json.dumps({"messages": [{"role": "user", "content": key}]}),
        provider="openai_compatible",
        model=model,
        result=StoredResult(
            content=f"answer {key}", usage=Usage(input_tokens=7, output_tokens=3), cost_usd=0.002
        ),
        created_at=NOW - timedelta(days=age_days),
    )


def _usage(call_id: str, *, hit: bool) -> UsageRecord:
    return UsageRecord(
        call_id=call_id,
        ts=NOW,
        day=NOW.date(),
        tier="quick",
        provider="openai_compatible",
        model="deepseek-flash",
        cost_usd=0.0 if hit else 0.002,
        latency_ms=1.0,
        cache_hit=hit,
    )


@pytest.fixture
def store(tmp_path: Path) -> Path:
    """A lake with three cache entries (1, 10 and 40 days old), two hits and one paid call."""
    lake = Lake(tmp_path / "lake")
    with Database(lake) as database:
        cache = LLMCache(database)
        for key, age, model in (
            ("a", 1, "deepseek-flash"),
            ("b", 10, "deepseek-flash"),
            ("c", 40, "deepseek-v4-pro"),
        ):
            cache.put(_entry(key, age, model))
        ledger = CostLedger(database)
        for record in (_usage("u1", hit=False), _usage("u2", hit=True), _usage("u3", hit=True)):
            ledger.record(record)
    return lake.root


def invoke(*args: str, env: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    return CliRunner().invoke(app, ["llm", "cache", *args], env={**BASE_ENV, **(env or {})})


def keys(store: Path) -> list[str]:
    with Database(Lake(store), read_only=True) as database:
        return [e.key for e in LLMCache(database).entries()]


def test_help_lists_the_three_commands() -> None:
    result = CliRunner().invoke(app, ["llm", "cache", "--help"], env=BASE_ENV)
    text = _plain(result.output)
    assert result.exit_code == 0
    for command in ("stats", "export", "prune"):
        assert command in text
    assert "llm" in _plain(CliRunner().invoke(app, ["--help"], env=BASE_ENV).output)


def test_stats(store: Path) -> None:
    result = invoke("stats", "--store", str(store))
    text = _plain(result.output)
    assert result.exit_code == 0, text
    assert re.search(r"entries\s+3", text)
    assert re.search(r"deepseek-flash\s+2", text) and re.search(r"deepseek-v4-pro\s+1", text)
    assert re.search(r"hits\s+2", text) and re.search(r"paid calls\s+1", text)
    assert "66.7%" in text  # 2 hits of 3 lookups
    assert str((NOW - timedelta(days=40)).date()) in text  # oldest


def test_stats_on_an_empty_cache_has_no_hit_rate(tmp_path: Path) -> None:
    lake = Lake(tmp_path / "empty")
    Database(lake).close()
    result = invoke("stats", "--store", str(lake.root))
    text = _plain(result.output)
    assert result.exit_code == 0, text
    assert re.search(r"entries\s+0", text) and "n/a" in text


def test_the_default_store_is_the_data_lake(store: Path, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "fund.yaml").write_text("fund: {benchmark: SPY}\n")
    result = CliRunner().invoke(
        app,
        ["--config-dir", str(config_dir), "llm", "cache", "stats"],
        env={**BASE_ENV, "ATLAS__DATA__LAKE_PATH": str(store)},
    )
    assert result.exit_code == 0, result.output
    assert re.search(r"entries\s+3", _plain(result.output))


def test_a_missing_store_exits_1_and_creates_nothing(tmp_path: Path) -> None:
    missing = tmp_path / "nowhere"
    result = invoke("stats", "--store", str(missing))
    assert result.exit_code == 1
    assert "No LLM cache" in _plain(result.output)
    assert not missing.exists()


def test_export_writes_one_json_line_per_entry_oldest_first(store: Path, tmp_path: Path) -> None:
    out = tmp_path / "out" / "cache.jsonl"
    result = invoke("export", str(out), "--store", str(store))
    assert result.exit_code == 0, _plain(result.output)
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert [line["key"] for line in lines] == ["c", "b", "a"]
    first = lines[0]
    assert set(first) == {"key", "created_at", "provider", "model", "request", "response", "usage"}
    assert first["response"]["content"] == "answer c" and first["model"] == "deepseek-v4-pro"
    assert first["usage"]["cost_usd"] == 0.002 and first["usage"]["usage"]["input_tokens"] == 7
    assert first["request"]["messages"][0]["content"] == "c"
    assert "3 entries" in _plain(result.output)


def test_export_refuses_to_overwrite_without_force(store: Path, tmp_path: Path) -> None:
    out = tmp_path / "cache.jsonl"
    out.write_text("keep me")
    refused = invoke("export", str(out), "--store", str(store))
    assert refused.exit_code == 1 and out.read_text() == "keep me"
    assert "--force" in _plain(refused.output)
    assert invoke("export", str(out), "--store", str(store), "--force").exit_code == 0
    assert len(out.read_text().splitlines()) == 3


def test_prune_dry_run_changes_nothing(store: Path) -> None:
    result = invoke("prune", "--older-than", "30d", "--dry-run", "--store", str(store))
    assert result.exit_code == 0, _plain(result.output)
    assert "would delete 1" in _plain(result.output)
    assert keys(store) == ["c", "b", "a"]


def test_prune_deletes_entries_older_than_the_age(store: Path) -> None:
    result = invoke("prune", "--older-than", "30d", "--store", str(store))
    assert result.exit_code == 0, _plain(result.output)
    assert "deleted 1" in _plain(result.output)
    assert keys(store) == ["b", "a"]
    assert invoke("prune", "--older-than", "5d", "--store", str(store)).exit_code == 0
    assert keys(store) == ["a"]


def test_prune_needs_older_than(store: Path) -> None:
    assert invoke("prune", "--store", str(store)).exit_code == 2


@pytest.mark.parametrize("bad", ["", "30", "d", "-3d", "1w", "2026-13-40", "0d", "tomorrow"])
def test_a_bad_older_than_is_a_usage_error(store: Path, bad: str) -> None:
    result = invoke("prune", "--older-than", bad, "--store", str(store))
    assert result.exit_code == 2
    assert keys(store) == ["c", "b", "a"]


def test_parse_older_than() -> None:
    now = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
    assert parse_older_than("30d", now) == now - timedelta(days=30)
    assert parse_older_than("12h", now) == now - timedelta(hours=12)
    assert parse_older_than("2026-11-01", now) == datetime(2026, 11, 1, tzinfo=UTC)
    assert parse_older_than(" 7d ", now) == now - timedelta(days=7)


def test_a_bad_config_dir_without_store_exits_1(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["--config-dir", str(tmp_path / "missing"), "llm", "cache", "stats"], env=BASE_ENV
    )
    assert result.exit_code == 1
    assert "Configuration error" in _plain(result.output)


def test_an_unreadable_database_exits_1_without_a_traceback(tmp_path: Path) -> None:
    # Audit A4: DuckDB refuses a corrupt file (or one another process holds open).
    root = tmp_path / "broken"
    root.mkdir()
    (root / "atlas.duckdb").write_bytes(b"not a duckdb file" * 100)
    result = invoke("stats", "--store", str(root))
    assert result.exit_code == 1
    assert "Cannot open the cache" in _plain(result.output)
    assert result.exception is None or isinstance(result.exception, SystemExit)
