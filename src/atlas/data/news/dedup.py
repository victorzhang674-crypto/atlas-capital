"""Duplicate stories: text normalisation and the content hash.

`normalize_text` folds a headline or summary to a comparable form (NFKC, lower case, every run
of characters that are not letters or digits turned into one space, stripped), so the same story
re-published with other punctuation, case or spacing compares equal. `content_hash` is the
SHA-256 hex digest of the normalised headline and summary.

`mark_duplicates` finds re-published copies of a story without looking into the future: rows
are ordered by (`available_at`, `article_id`), and a row is a duplicate when an *earlier* row
at most `dedup_window_hours` before it has the same content hash or a headline scoring at least
`fuzzy_threshold` (rapidfuzz `ratio` of the normalised headlines). The earliest-known copy is
the original, so once an article has been seen as the original it stays one. Whether a row is
a duplicate depends only on the rows in the window before it: marking any time range gives the
same answer inside it as marking all history, provided one window of earlier rows is included.
"""

import hashlib
import re
import unicodedata

import pandas as pd
from rapidfuzz import fuzz

from atlas.core.config import NewsConfig

DUPLICATE_OF = "duplicate_of"
"""Column added by `mark_duplicates`: the original's `article_id`, or `None` for originals."""

_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def normalize_text(text: str) -> str:
    """`text` folded for comparison: NFKC, lower case, non-alphanumeric runs → one space."""
    folded = unicodedata.normalize("NFKC", text).lower()
    return _NON_ALNUM.sub(" ", folded).strip()


def content_hash(headline: str, summary: str) -> str:
    """SHA-256 hex of the normalised headline and summary (separated by a newline)."""
    body = normalize_text(headline) + "\n" + normalize_text(summary)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def mark_duplicates(articles: pd.DataFrame, settings: NewsConfig | None = None) -> pd.DataFrame:
    """A copy of `articles` (article frame) with `DUPLICATE_OF` added, rows in input order.

    `duplicate_of` names the original of a duplicate's story (the root when copies chain:
    a copy of a copy points at the first article), and is `None` for an original. Headlines
    that normalise to nothing are matched by content hash only.
    """
    cfg = settings if settings is not None else NewsConfig()
    out = articles.copy()
    if out.empty:
        out[DUPLICATE_OF] = pd.Series([], dtype="object")
        return out
    ids = [str(v) for v in out["article_id"]]
    times = list(out["available_at"])
    hashes = list(out["content_hash"])
    heads = [normalize_text(str(h)) for h in out["headline"]]
    window = pd.Timedelta(hours=cfg.dedup_window_hours)
    order = sorted(range(len(ids)), key=lambda i: (times[i], ids[i]))
    root: list[int | None] = [None] * len(ids)
    first = 0
    for pos, i in enumerate(order):
        while times[order[first]] < times[i] - window:
            first += 1
        for j in order[first:pos]:
            if hashes[j] == hashes[i] or _similar(heads[j], heads[i], cfg.fuzzy_threshold):
                root[i] = j if root[j] is None else root[j]
                break
    out[DUPLICATE_OF] = pd.Series(
        [None if r is None else ids[r] for r in root], index=out.index, dtype="object"
    )
    return out


def _similar(a: str, b: str, threshold: float) -> bool:
    if not a or not b:
        return False
    return fuzz.ratio(a, b, score_cutoff=threshold) >= threshold
