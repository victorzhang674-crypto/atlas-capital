"""Light relevance scoring of news for one ticker, and the roundup filter.

Candidates are the articles the vendor tagged with the ticker. A headline *names* the ticker
when it contains `$S`, `(S)`, an exchange prefix (`NYSE:S`, `NASDAQ: S`, ...), or, for tickers
of three or more characters, the bare upper-case symbol; share classes also match the `-` and
`/` spellings (`BRK.B`, `BRK-B`, `BRK/B`). One- and two-letter tickers need a marker because
`T`, `F` or `GM` also appear as ordinary words. The match is never preceded or followed by a
letter or digit and is case-sensitive. Company names are not matched (they give false
positives such as "Target" or "Apple" the fruit).

Relevance is `headline_relevance` (1.0) when the headline names the ticker, else
`symbols_relevance` (0.5). An article tagged with more than `roundup_max_symbols` (10) tickers
is a roundup and is dropped unless its headline names the ticker.
"""

import re
from functools import lru_cache

import pandas as pd

from atlas.core.config import NewsConfig
from atlas.core.types.instruments import normalize_symbol

_EXCHANGES = "NYSE|NASDAQ|NYSEARCA|NYSEAMERICAN|AMEX|ARCA|BATS|CBOE|OTC"
_BEFORE, _AFTER = r"(?<![A-Za-z0-9])", r"(?![A-Za-z0-9])"


@lru_cache(maxsize=4096)
def _pattern(symbol: str) -> re.Pattern[str]:
    spellings = [symbol]
    if "." in symbol:
        spellings += [symbol.replace(".", "-"), symbol.replace(".", "/")]
    names = "|".join(re.escape(s) for s in spellings)
    forms = [rf"\$(?:{names})", rf"\((?:{names})\)", rf"(?:{_EXCHANGES})\s*:\s*(?:{names})"]
    if len(symbol) >= 3:
        forms.append(rf"(?:{names})")
    return re.compile(_BEFORE + "(?:" + "|".join(forms) + ")" + _AFTER)


def headline_mentions(headline: str, symbol: str) -> bool:
    """Whether `headline` names `symbol` (any accepted spelling) by the module's rules."""
    return _pattern(normalize_symbol(symbol)).search(headline) is not None


def score_relevance(
    articles: pd.DataFrame, symbol: str, settings: NewsConfig | None = None
) -> pd.DataFrame:
    """The rows of `articles` tagged with `symbol`, roundups dropped, with `relevance` (float)
    and `headline_match` (bool) added; input order kept."""
    cfg = settings if settings is not None else NewsConfig()
    sym = normalize_symbol(symbol)
    tagged = articles.loc[articles["symbols"].map(lambda tags: sym in tags).astype(bool)].copy()
    match = tagged["headline"].map(lambda h: headline_mentions(str(h), sym)).astype(bool)
    roundup = tagged["symbols"].map(len) > cfg.roundup_max_symbols
    out = tagged.loc[match | ~roundup].copy()
    out["headline_match"] = match.loc[out.index].astype(bool)
    out["relevance"] = (
        out["headline_match"]
        .map({True: cfg.headline_relevance, False: cfg.symbols_relevance})
        .astype("float64")
    )
    return out
