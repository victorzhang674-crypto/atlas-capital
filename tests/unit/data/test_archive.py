"""Phase 1 checkpoint, Tier 4: the raw vendor archive (`atlas.data.archive`).

Prices and news were kept only as curated rows, where a later fetch overwrites an earlier one
(edited headlines, a vendor's changed adjusted close); the archive keeps every answer as it
arrived, under `<lake>/raw/<vendor>/<dataset>/<UTC date>/`."""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest
from price_helpers import FakePriceProvider, daily_rows

from atlas.core.clock import SimClock, use_clock
from atlas.core.config import AtlasConfig
from atlas.core.storage import Lake
from atlas.data.archive import ArchivingNewsProvider, ArchivingPriceProvider, archive_frame
from atlas.data.news.base import RAW_COLUMNS, normalize_news

D1, D2 = date(2024, 3, 1), date(2024, 3, 4)
NOW = datetime(2026, 11, 3, 14, 30, 5, tzinfo=UTC)


def _files(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*.parquet"))


class _News:
    """A `NewsProvider` answering every request with `frame`."""

    def __init__(self, name: str, frame: pd.DataFrame) -> None:
        self.name, self.frame = name, frame

    def fetch_news(
        self, symbols: Sequence[str] | None, start: datetime, end: datetime
    ) -> pd.DataFrame:
        return self.frame


def _articles(*headlines: str) -> pd.DataFrame:
    rows = [
        {"article_id": f"alpaca-{i}", "published_at": datetime(2020, 3, 2, 15, tzinfo=UTC),
         "updated_at": None, "provider": "alpaca", "source": "benzinga", "headline": h,
         "summary": "", "url": None, "symbols": ["AAPL"]}
        for i, h in enumerate(headlines)
    ]  # fmt: skip
    return normalize_news(pd.DataFrame(rows, columns=list(RAW_COLUMNS)))


def _daily(vendor: str, *symbols: str) -> pd.DataFrame:
    frames = [daily_rows(s, [(D1, 10.0, 100), (D2, 11.0, 100)], vendor) for s in symbols]
    return pd.concat(frames, ignore_index=True)


def test_price_answers_are_archived_per_vendor(tmp_path: Path) -> None:
    mixed = pd.concat([_daily("yfinance", "AAPL"), _daily("alpaca", "AVB")], ignore_index=True)
    inner = FakePriceProvider(mixed, name="fallback")
    provider = ArchivingPriceProvider(inner, tmp_path)
    assert provider.name == "fallback" and provider.inner is inner
    with use_clock(SimClock(start=NOW)):
        out = provider.fetch_daily(["AAPL", "AVB"], D1, D2)
        again = provider.fetch_daily(["AAPL", "AVB"], D1, D2)  # same answer, same second
    pd.testing.assert_frame_equal(out, inner.fetch_daily(["AAPL", "AVB"], D1, D2))
    pd.testing.assert_frame_equal(again, out)
    files = _files(tmp_path)
    assert [f.rsplit("/", 1)[0] for f in files] == [
        "alpaca/daily/2026-11-03",
        "yfinance/daily/2026-11-03",
    ]
    assert all(f.rsplit("/", 1)[1].startswith("143005-") for f in files)
    stored = pd.read_parquet(tmp_path / files[0])
    assert list(stored["symbol"].unique()) == ["AVB"] and len(stored) == 2


def test_empty_answer_writes_nothing(tmp_path: Path) -> None:
    provider = ArchivingPriceProvider(FakePriceProvider(_daily("yfinance", "AAPL")), tmp_path)
    with use_clock(SimClock(start=NOW)):
        assert provider.fetch_daily(["ZZZ"], D1, D2).empty
        assert provider.fetch_actions(["AAPL"], D1, D2).empty
    assert _files(tmp_path) == []


def test_record_served_and_close_pass_through(tmp_path: Path) -> None:
    calls: list[str] = []

    class Inner(FakePriceProvider):
        def record_served(self, daily: pd.DataFrame) -> None:
            calls.append("served")

        def close(self) -> None:
            calls.append("closed")

    provider = ArchivingPriceProvider(Inner(_daily("yfinance", "AAPL")), tmp_path)
    provider.record_served(_daily("yfinance", "AAPL"))
    provider.close()
    plain = ArchivingPriceProvider(FakePriceProvider(_daily("yfinance", "AAPL")), tmp_path)
    plain.record_served(_daily("yfinance", "AAPL"))  # no recorder inside: nothing to do
    plain.close()
    assert calls == ["served", "closed"]


def test_news_answers_are_archived(tmp_path: Path) -> None:
    inner = _News("alpaca", _articles("Apple shares rise", "Apple falls"))
    provider = ArchivingNewsProvider(inner, tmp_path)
    start, end = datetime(2020, 3, 2, tzinfo=UTC), datetime(2020, 3, 3, tzinfo=UTC)
    with use_clock(SimClock(start=NOW)):
        out = provider.fetch_news(["AAPL"], start, end)
    assert len(out) == 2 and provider.name == "alpaca" and provider.inner is inner
    (only,) = _files(tmp_path)
    assert only.startswith("alpaca/news/2026-11-03/")
    stored = pd.read_parquet(tmp_path / only)
    assert sorted(stored["headline"]) == ["Apple falls", "Apple shares rise"]


def test_archive_frame_needs_a_vendor_column(tmp_path: Path) -> None:
    with use_clock(SimClock(start=NOW)), pytest.raises(KeyError, match="vendor"):
        archive_frame(tmp_path, "daily", pd.DataFrame({"x": [1]}), vendor_column="vendor")


def test_sync_wraps_its_sources_only_when_configured(tmp_lake: Lake) -> None:
    from atlas.data.sync import SyncSources

    on = SyncSources.live(AtlasConfig(), tmp_lake)
    assert isinstance(on.price_provider(), ArchivingPriceProvider)
    assert isinstance(on.news_provider(), ArchivingNewsProvider)
    off_cfg = AtlasConfig.model_validate({"data": {"sync": {"archive_raw": False}}})
    off = SyncSources.live(off_cfg, tmp_lake)
    assert not isinstance(off.price_provider(), ArchivingPriceProvider)
    assert not isinstance(off.news_provider(), ArchivingNewsProvider)
