"""`atlas data migrate`: bring stored tables up to their registered schema version, after a
backup of the tables it will rewrite (audit follow-up: the news and news_sentiment v2 schemas)."""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from atlas.cli.app import app
from atlas.core.storage import Lake, SchemaRegistry
from atlas.data.news import NEWS, normalize_news
from atlas.data.news.base import RAW_COLUMNS

REPO_ROOT = Path(__file__).resolve().parents[3]


def _old_news_lake(root: Path) -> Lake:
    """A lake whose `news` table was written by the version-1 schema (old primary key)."""
    lake = Lake(root, registry=_v1_registry())
    row = {
        "article_id": "alpaca-1",
        "published_at": datetime(2020, 3, 2, 15, tzinfo=UTC),
        "updated_at": None,
        "provider": "alpaca",
        "source": "benzinga",
        "headline": "Apple shares rise",
        "summary": "",
        "url": None,
        "symbols": ["AAPL"],
    }
    lake.write_table("news", normalize_news(pd.DataFrame([row], columns=list(RAW_COLUMNS))))
    return lake


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    monkeypatch.chdir(tmp_path)
    _old_news_lake(tmp_path / "lake")
    return {"COLUMNS": "200", "NO_COLOR": "1", "ATLAS__DATA__LAKE_PATH": str(tmp_path / "lake")}


def run(env: dict[str, str], *args: str):
    return CliRunner().invoke(app, ["data", "migrate", *args], env=env)


def version(root: Path, table: str) -> int:
    meta = json.loads((root / "curated" / "_meta.json").read_text())
    return int(meta["tables"][table]["version"])


def test_dry_run_lists_the_pending_tables_and_changes_nothing(
    env: dict[str, str], tmp_path: Path
) -> None:
    result = run(env, "--dry-run")
    assert result.exit_code == 0, result.output
    assert "news" in result.output and "1" in result.output and "2" in result.output
    assert version(tmp_path / "lake", "news") == 1
    assert not list((tmp_path / "lake" / "cache").glob("backup-migrate-*"))


def test_migrate_backs_up_then_migrates_and_keeps_the_rows(
    env: dict[str, str], tmp_path: Path
) -> None:
    root = tmp_path / "lake"
    before = Lake(root, registry=_v1_registry()).read_table("news")
    result = run(env)
    assert result.exit_code == 0, result.output
    assert version(root, "news") == 2
    after = Lake(root).read_table("news")
    pd.testing.assert_frame_equal(
        before.reset_index(drop=True), after.reset_index(drop=True), check_like=True
    )
    [backup] = list((root / "cache").glob("backup-migrate-*"))
    assert str(backup) in result.output
    saved = json.loads((backup / "_meta.json").read_text())
    assert saved["tables"]["news"]["version"] == 1  # the backup is the old state
    assert list((backup / "news").rglob("*.parquet"))


def test_a_second_run_has_nothing_to_do(env: dict[str, str], tmp_path: Path) -> None:
    assert run(env).exit_code == 0
    again = run(env)
    assert again.exit_code == 0 and "Nothing to migrate" in again.output
    assert len(list((tmp_path / "lake" / "cache").glob("backup-migrate-*"))) == 1


def test_an_unknown_table_is_a_usage_error(env: dict[str, str]) -> None:
    result = run(env, "--tables", "nope")
    assert result.exit_code == 2 and "nope" in result.output


def test_an_existing_backup_folder_is_refused_and_nothing_changes(
    env: dict[str, str], tmp_path: Path
) -> None:
    folder = tmp_path / "bk"
    folder.mkdir()
    result = run(env, "--backup-dir", str(folder))
    assert result.exit_code == 1 and "already exists" in result.output
    assert version(tmp_path / "lake", "news") == 1


def test_a_failing_migration_names_the_backup_and_leaves_the_table(
    env: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from atlas.core.errors import DataError

    def boom(self: Lake, name: str) -> int:
        raise DataError("disk full")

    monkeypatch.setattr(Lake, "migrate", boom)
    result = run(env)
    assert result.exit_code == 1
    assert "disk full" in result.output and "backup" in result.output.lower()
    assert version(tmp_path / "lake", "news") == 1


def _v1_registry() -> SchemaRegistry:
    v1 = NEWS.model_copy(update={"version": 1, "primary_key": ("article_id", "published_at")})
    return SchemaRegistry([v1])
