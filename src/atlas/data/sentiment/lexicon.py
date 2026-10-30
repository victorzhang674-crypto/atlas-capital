"""Loughran-McDonald finance lexicon: build, load, score text, and the `news_sentiment` table.

The Loughran-McDonald master dictionary (sraf.nd.edu; free for academic research, commercial
licences on request, so it is downloaded into the gitignored lake, never committed) lists every
word with, per sentiment category, the year it was added (> 0) or removed (< 0).
`build_lm_lexicon` keeps the words of the `negative`, `positive` and `uncertainty` lists, with
the years they were added or removed, as a `word,category,added,removed` table;
`download_lm_lexicon` fetches the master dictionary and writes that table to
`SentimentConfig.lexicon_file`.

Scoring (`score_text`): words are runs of ASCII letters, upper-cased. A positive word with one
of NO, NOT, NONE, NEITHER, NEVER, NOBODY among the `negation_window` words before it is not
counted (Loughran and McDonald 2011). `score = (pos - neg) / (pos + neg)` in [-1, 1], 0.0 when
the text has neither; `pos`, `neg`, `uncertainty` and `words` are counts. A word may be in two
lists (VOLATILITY is negative and uncertain) and counts in each. An article's text is its
headline and summary, scored with the lists as they stood in its publish year (17 words left
the lists in 2020; they still count in earlier news).
"""

import hashlib
import io
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Final, NamedTuple

import httpx
import pandas as pd

from atlas.core.config import AtlasConfig, SentimentConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.storage import (
    AVAILABLE_AT,
    DEFAULT_REGISTRY,
    YEAR,
    Column,
    ColumnType,
    Lake,
    TableSchema,
)
from atlas.core.storage.meta import atomic_write
from atlas.data.news import News

_log = get_logger(__name__)

CATEGORIES: Final = ("negative", "positive", "uncertainty")
"""Lexicon categories used, as `word,category` values."""

_MASTER_COLUMNS: Final = {
    "negative": "Negative",
    "positive": "Positive",
    "uncertainty": "Uncertainty",
}
NEGATIONS: Final = frozenset({"NO", "NOT", "NONE", "NEITHER", "NEVER", "NOBODY"})
"""Words that cancel a positive word within the negation window (Loughran-McDonald 2011)."""

_WORD = re.compile(r"[A-Za-z]+")

SCORE_COLUMNS: Final = (
    "article_id",
    "published_at",
    AVAILABLE_AT,
    "score",
    "pos",
    "neg",
    "uncertainty",
    "words",
    "lexicon_version",
)
"""Columns of `score_articles` and of the `news_sentiment` table, in order."""

NEWS_SENTIMENT = DEFAULT_REGISTRY.register(
    TableSchema(
        name="news_sentiment",
        columns=(
            Column(name="article_id", dtype=ColumnType.STRING),
            Column(name="published_at", dtype=ColumnType.TIMESTAMP),
            Column(name=AVAILABLE_AT, dtype=ColumnType.TIMESTAMP),
            Column(name="score", dtype=ColumnType.FLOAT64),
            Column(name="pos", dtype=ColumnType.INT64),
            Column(name="neg", dtype=ColumnType.INT64),
            Column(name="uncertainty", dtype=ColumnType.INT64),
            Column(name="words", dtype=ColumnType.INT64),
            Column(name="lexicon_version", dtype=ColumnType.STRING),
        ),
        primary_key=("article_id", "published_at"),
        partition_by=(YEAR,),
        year_column="published_at",
        description="Lexicon sentiment per news article (score in [-1, 1], word counts); Stage 12",
    )
)
"""The curated `news_sentiment` table (registered in `DEFAULT_REGISTRY` on import)."""


Span = tuple[str, str, int | None, int | None]
"""`(word, category, added, removed)`: in the list from year `added` (`None`: from the start)
until the year before `removed` (`None`: still in it)."""


@dataclass(frozen=True)
class Lexicon:
    """Upper-case word sets per category, as the lists stand now; `version` identifies the word
    lists (the first 12 hex digits of the sha256 of the sorted lines of the file). `spans`
    records when each word was in its list, for `for_year`."""

    positive: frozenset[str]
    negative: frozenset[str]
    uncertainty: frozenset[str]
    version: str
    spans: tuple[Span, ...] = ()

    def for_year(self, year: int) -> "Lexicon":
        """The lists as they stood in `year` (a word counts when `added <= year < removed`);
        the lexicon itself when it has no year information."""
        if not self.spans:
            return self
        sets: dict[str, set[str]] = {c: set() for c in CATEGORIES}
        for word, category, added, removed in self.spans:
            if (added is None or added <= year) and (removed is None or year < removed):
                sets[category].add(word)
        return Lexicon(
            frozenset(sets["positive"]),
            frozenset(sets["negative"]),
            frozenset(sets["uncertainty"]),
            self.version,
            self.spans,
        )


class TextSentiment(NamedTuple):
    """Sentiment of one text: `score` in [-1, 1] and word counts (see the module docstring)."""

    score: float
    pos: int
    neg: int
    uncertainty: int
    words: int


def build_lm_lexicon(master: pd.DataFrame) -> pd.DataFrame:
    """The `word,category,added,removed` table of the master dictionary `master` (columns
    `Word`, `Negative`, `Positive`, `Uncertainty`). A value above 0 is the year the word was
    added to that category (`added`); below 0, minus the year it was removed (`removed`; it was
    in the list before). Sorted by category, then word. A missing column raises `DataError`."""
    needed = ["Word", *_MASTER_COLUMNS.values()]
    missing = [c for c in needed if c not in master.columns]
    if missing:
        raise DataError(f"LM master dictionary lacks columns {missing}")
    parts = []
    for category, column in _MASTER_COLUMNS.items():
        values = pd.to_numeric(master[column], errors="coerce").fillna(0).astype("int64")
        listed = values != 0
        parts.append(
            pd.DataFrame(
                {
                    "word": master.loc[listed, "Word"].astype(str).str.strip().str.upper(),
                    "category": category,
                    "added": values[listed].where(values[listed] > 0).astype("Int64"),
                    "removed": (-values[listed]).where(values[listed] < 0).astype("Int64"),
                }
            )
        )
    out = pd.concat(parts, ignore_index=True).drop_duplicates(subset=["word", "category"])
    out = out.loc[out["word"] != ""]
    return out.sort_values(["category", "word"], kind="stable").reset_index(drop=True)


def download_lm_lexicon(config: SentimentConfig, *, http: httpx.Client | None = None) -> Path:
    """Download the master dictionary from `config.lexicon_url`, convert it with
    `build_lm_lexicon` and write it atomically to `config.lexicon_file` (relative to the
    working directory); returns that path. HTTP or format errors raise `DataError`."""
    client = http if http is not None else httpx.Client(timeout=120, follow_redirects=True)
    try:
        try:
            resp = client.get(config.lexicon_url)
        except httpx.TransportError as exc:
            raise DataError(f"LM lexicon download failed: {exc}") from exc
        if resp.status_code != 200:
            raise DataError(f"LM lexicon download returned HTTP {resp.status_code}")
        try:
            master = pd.read_csv(io.BytesIO(resp.content))
        except (ValueError, UnicodeDecodeError) as exc:
            raise DataError(f"LM lexicon download is not a CSV ({exc})") from exc
    finally:
        if http is None:
            client.close()
    table = build_lm_lexicon(master)
    if table.empty:
        raise DataError("LM lexicon download holds no sentiment words")

    def _write(fh: BinaryIO) -> None:
        fh.write(table.to_csv(index=False).encode("utf-8"))

    atomic_write(config.lexicon_file, _write)
    _log.info("sentiment.lexicon_written", path=str(config.lexicon_file), words=len(table))
    return config.lexicon_file


def load_lexicon(path: Path) -> Lexicon:
    """The lexicon in `path`: `word,category,added,removed` (years, blank when none) or the
    older `word,category` (no year information). A missing file, other columns, an unknown
    category or a bad year raises `DataError` (a missing file names `download_lm_lexicon`)."""
    if not path.is_file():
        raise DataError(f"{path}: lexicon not found; run download_lm_lexicon first")
    table = pd.read_csv(path, dtype=str, keep_default_na=False)
    columns = list(table.columns)
    if columns not in (["word", "category"], ["word", "category", "added", "removed"]):
        raise DataError(f"{path}: expected columns word,category[,added,removed], got {columns}")
    unknown = sorted(set(table["category"]) - set(CATEGORIES))
    if unknown:
        raise DataError(f"{path}: unknown categories {unknown}")
    words = table["word"].str.strip().str.upper()
    years = len(columns) == 4
    try:
        added = [_year(v) for v in table["added"]] if years else [None] * len(table)
        removed = [_year(v) for v in table["removed"]] if years else [None] * len(table)
    except ValueError as exc:
        raise DataError(f"{path}: bad year ({exc})") from exc
    spans = tuple(zip(words, table["category"], added, removed, strict=True))
    current = {c: frozenset(w for w, k, _, r in spans if k == c and r is None) for c in CATEGORIES}
    if years:
        lines = sorted(f"{k},{w},{a or ''},{r or ''}" for w, k, a, r in spans)
    else:
        lines = sorted(f"{k},{w}" for w, k, _, _ in spans)
    version = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:12]
    return Lexicon(
        current["positive"],
        current["negative"],
        current["uncertainty"],
        version,
        spans if years else (),
    )


def _year(value: str) -> int | None:
    """A year cell: blank → `None`; `2009` or `2009.0` (a pandas round trip) → 2009."""
    if not value.strip():
        return None
    number = float(value)
    if not number.is_integer():
        raise ValueError(f"{value!r} is not a whole year")
    return int(number)


def tokenize(text: str) -> list[str]:
    """The words of `text`: runs of ASCII letters, upper-cased."""
    return [w.upper() for w in _WORD.findall(text)]


def score_text(text: str, lexicon: Lexicon, negation_window: int = 3) -> TextSentiment:
    """The lexicon sentiment of `text` (see the module docstring)."""
    tokens = tokenize(text)
    pos = sum(
        1
        for i, w in enumerate(tokens)
        if w in lexicon.positive
        and not NEGATIONS.intersection(tokens[max(0, i - negation_window) : i])
    )
    neg = sum(1 for w in tokens if w in lexicon.negative)
    uncertain = sum(1 for w in tokens if w in lexicon.uncertainty)
    score = (pos - neg) / (pos + neg) if pos + neg else 0.0
    return TextSentiment(score, pos, neg, uncertain, len(tokens))


def score_articles(
    articles: pd.DataFrame, lexicon: Lexicon, settings: SentimentConfig | None = None
) -> pd.DataFrame:
    """`SCORE_COLUMNS` for each article of `articles` (Stage 11 article frame; headline plus
    summary scored with the lexicon of the article's publish year, `Lexicon.for_year`), in the
    order given."""
    cfg = settings if settings is not None else SentimentConfig()
    by_year: dict[int, Lexicon] = {}
    scores = []
    for h, s, published in zip(
        articles["headline"], articles["summary"], articles["published_at"], strict=True
    ):
        year = pd.Timestamp(published).year
        if year not in by_year:
            by_year[year] = lexicon.for_year(year)
        scores.append(score_text(f"{h} {s}", by_year[year], cfg.negation_window))
    out = pd.DataFrame(
        {
            "article_id": articles["article_id"].astype("str").to_numpy(),
            "published_at": articles["published_at"].to_numpy(),
            AVAILABLE_AT: articles[AVAILABLE_AT].to_numpy(),
            "score": pd.Series([s.score for s in scores], dtype="float64"),
            "pos": pd.Series([s.pos for s in scores], dtype="int64"),
            "neg": pd.Series([s.neg for s in scores], dtype="int64"),
            "uncertainty": pd.Series([s.uncertainty for s in scores], dtype="int64"),
            "words": pd.Series([s.words for s in scores], dtype="int64"),
            "lexicon_version": pd.Series([lexicon.version] * len(scores), dtype="str"),
        }
    )
    for col in ("published_at", AVAILABLE_AT):
        out[col] = pd.to_datetime(out[col], utc=True).dt.as_unit("us")
    return out[list(SCORE_COLUMNS)]


def ingest_news_sentiment(
    lake: Lake,
    lexicon: Lexicon,
    start: datetime,
    end: datetime,
    *,
    config: AtlasConfig | None = None,
) -> int:
    """Score the stored articles with `available_at` in `[start, end)` (aware) and upsert them
    into `news_sentiment` (idempotent); returns the rows written. An unwritten `news` table
    raises `DataError`."""
    cfg = config if config is not None else AtlasConfig()
    frame = News(lake, cfg).articles(start, end)
    if frame.empty:
        return 0
    scored = score_articles(frame, lexicon, cfg.data.sentiment)
    lake.write_table(
        NEWS_SENTIMENT.name, scored, mode="upsert", keys=list(NEWS_SENTIMENT.primary_key)
    )
    _log.info("sentiment.ingest", rows=len(scored), lexicon=lexicon.version)
    return len(scored)
