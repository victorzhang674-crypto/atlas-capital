"""The news provider contract, the article frame and the curated `news` table.

Article frame (`ARTICLE_COLUMNS`, the columns of `news`), one row per vendor article:

| Column | Type | Meaning |
|---|---|---|
| `article_id` | `str` | `<provider>-<vendor id>` (`alpaca-24843171`, `yahoo-<uuid>`) |
| `published_at` | UTC timestamp | the vendor's publish time |
| `available_at` | UTC timestamp | equal to `published_at` (known from publication) |
| `updated_at` | UTC timestamp or NaT | the vendor's last edit (audit only) |
| `provider` | `str` | adapter that served the row (`alpaca`, `yahoo`) |
| `source` | `str` | publisher (`benzinga`, `Reuters`, ...) |
| `headline` | `str` | non-empty, stripped |
| `summary` | `str` | may be `""`; at most `NewsConfig.max_summary_chars` characters |
| `url` | `str` or `None` | link to the article |
| `symbols` | `tuple[str, ...]` | canonical tickers the vendor tagged, sorted, de-duplicated |
| `content_hash` | `str` | `dedup.content_hash(headline, summary)` |

`article_id` depends only on the vendor id, so it is stable across re-ingests. Only the
headline and summary are stored (copyright and size); article bodies and images are
never requested or kept. Providers return the raw columns (`RAW_COLUMNS`) through
`normalize_news`, and raise `DataError` when the vendor fails after retries.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Final, Protocol, runtime_checkable

import pandas as pd

from atlas.core.config import NewsConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.storage import DEFAULT_REGISTRY, YEAR, Column, ColumnType, TableSchema
from atlas.core.types.instruments import normalize_symbol
from atlas.data.news.dedup import content_hash

ARTICLE_COLUMNS: Final = (
    "article_id",
    "published_at",
    "available_at",
    "updated_at",
    "provider",
    "source",
    "headline",
    "summary",
    "url",
    "symbols",
    "content_hash",
)
"""Columns of an article frame and of the `news` table, in order."""

RAW_COLUMNS: Final = tuple(c for c in ARTICLE_COLUMNS if c not in ("available_at", "content_hash"))
"""Columns a provider fills before `normalize_news` adds `available_at` and `content_hash`."""

NEWS = DEFAULT_REGISTRY.register(
    TableSchema(
        name="news",
        columns=(
            Column(name="article_id", dtype=ColumnType.STRING),
            Column(name="published_at", dtype=ColumnType.TIMESTAMP),
            Column(name="available_at", dtype=ColumnType.TIMESTAMP),
            Column(name="updated_at", dtype=ColumnType.TIMESTAMP, nullable=True),
            Column(name="provider", dtype=ColumnType.STRING),
            Column(name="source", dtype=ColumnType.STRING),
            Column(name="headline", dtype=ColumnType.STRING),
            Column(name="summary", dtype=ColumnType.STRING),
            Column(name="url", dtype=ColumnType.STRING, nullable=True),
            Column(name="symbols", dtype=ColumnType.STRING_LIST),
            Column(name="content_hash", dtype=ColumnType.STRING),
        ),
        primary_key=("article_id", "published_at"),
        partition_by=(YEAR,),
        year_column="published_at",
        description="News headlines and summaries, one row per vendor article (Stage 11)",
    )
)
"""The curated `news` table (registered in `DEFAULT_REGISTRY` on import)."""

_log = get_logger(__name__)


@runtime_checkable
class NewsProvider(Protocol):
    """A source of timestamped news articles (see the module docstring for the frame)."""

    name: str

    def fetch_news(
        self, symbols: Sequence[str] | None, start: datetime, end: datetime
    ) -> pd.DataFrame:
        """Articles tagged with any of `symbols` (`None`: market-wide, every article) published
        in `[start, end)`, as an article frame."""
        ...


def check_news_window(
    symbols: Sequence[str] | None, start: datetime, end: datetime
) -> tuple[str, ...] | None:
    """Canonical symbols of a request (de-duplicated, first-seen order), or `None` for a
    market-wide request. `ValueError` for a bare string, an invalid symbol, a naive bound or
    `start` not before `end`."""
    for bound in (start, end):
        if not isinstance(bound, datetime) or bound.utcoffset() is None:
            raise ValueError(f"start and end must be timezone-aware datetimes, got {bound!r}")
    if start >= end:
        raise ValueError(f"start ({start.isoformat()}) must be before end ({end.isoformat()})")
    if symbols is None:
        return None
    if isinstance(symbols, str):
        raise ValueError("symbols must be a sequence of tickers, not one string")
    return tuple(dict.fromkeys(normalize_symbol(s) for s in symbols))


def empty_news() -> pd.DataFrame:
    """An article frame with no rows."""
    return _typed(pd.DataFrame({c: pd.Series([], dtype="object") for c in ARTICLE_COLUMNS}))


def normalize_news(df: pd.DataFrame, settings: NewsConfig | None = None) -> pd.DataFrame:
    """Raw provider rows (`RAW_COLUMNS`) as an article frame.

    Headlines and summaries are stripped (a missing summary is `""`), summaries cut to
    `settings.max_summary_chars`, rows without a headline dropped; tags are canonical symbols,
    de-duplicated and sorted (a tag that is not a valid symbol, such as `^GSPC`, is dropped);
    `available_at = published_at` in UTC; `content_hash` is added; the first row of each
    `article_id` is kept; rows are sorted by `published_at`, `article_id`. A naive or missing
    `published_at` raises `DataError`.
    """
    cfg = settings if settings is not None else NewsConfig()
    if not isinstance(df, pd.DataFrame):
        raise DataError(f"news: expected a pandas DataFrame, got {type(df).__name__}")
    missing = [c for c in RAW_COLUMNS if c not in df.columns]
    if missing:
        raise DataError(f"news: missing columns {missing}")
    if df.empty:
        return empty_news()
    headlines = [_text(v).strip() for v in df["headline"]]
    keep = [bool(h) for h in headlines]
    frame = df.loc[keep].reset_index(drop=True)
    if frame.empty:
        return empty_news()
    headlines = [h for h, k in zip(headlines, keep, strict=True) if k]
    summaries = [_text(v).strip()[: cfg.max_summary_chars] for v in frame["summary"]]
    published = _instants(frame["published_at"], "published_at", nullable=False)
    out = pd.DataFrame(
        {
            "article_id": [str(v) for v in frame["article_id"]],
            "published_at": published,
            "available_at": published,
            "updated_at": _instants(frame["updated_at"], "updated_at", nullable=True),
            "provider": [str(v) for v in frame["provider"]],
            "source": [str(v) for v in frame["source"]],
            "headline": headlines,
            "summary": summaries,
            "url": [_optional_text(v) for v in frame["url"]],
            "symbols": [_tags(v) for v in frame["symbols"]],
            "content_hash": [content_hash(h, s) for h, s in zip(headlines, summaries, strict=True)],
        }
    )
    out = out.drop_duplicates(subset=["article_id"], keep="first")
    out = out.sort_values(["published_at", "article_id"], kind="stable").reset_index(drop=True)
    return _typed(out)


def _typed(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for col in ("published_at", "available_at", "updated_at"):
        out[col] = pd.to_datetime(out[col], utc=True).dt.as_unit("us")
    for col in ("article_id", "provider", "source", "headline", "summary", "content_hash"):
        out[col] = out[col].astype("str")
    out["url"] = pd.Series([_optional_text(v) for v in out["url"]], index=out.index, dtype="object")
    out["symbols"] = pd.Series(list(out["symbols"]), index=out.index, dtype="object")
    return out[list(ARTICLE_COLUMNS)]


def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value)


def _optional_text(value: Any) -> str | None:
    text = _text(value).strip()
    return text or None


def _instants(series: pd.Series, name: str, *, nullable: bool) -> list[pd.Timestamp | None]:
    out: list[pd.Timestamp | None] = []
    for value in series:
        if value is None or value is pd.NaT or (not isinstance(value, datetime) and pd.isna(value)):
            if not nullable:
                raise DataError(f"news: {name} is missing")
            out.append(None)
            continue
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise DataError(f"news: {name} must be a timezone-aware datetime, got {value!r}")
        out.append(pd.Timestamp(value).tz_convert("UTC"))
    return out


def _tags(value: Any) -> tuple[str, ...]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        raw: list[Any] = []
    elif isinstance(value, str):
        raw = [value]
    else:
        raw = list(value)
    tags = set()
    for item in raw:
        try:
            tags.add(normalize_symbol(str(item)))
        except ValueError:
            _log.debug("news.bad_symbol", tag=str(item))
    return tuple(sorted(tags))
