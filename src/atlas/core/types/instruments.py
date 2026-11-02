"""Tradable instruments and ticker symbols.

Inside Atlas a symbol has one canonical form: upper case, with a dot before a share class
(`BRK.B`). Vendors spell class shares differently (yfinance `BRK-B`); `normalize_symbol`
reads any of those spellings and `vendor_symbol` writes one back out.
"""

import re
from collections.abc import Mapping
from datetime import date
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AfterValidator, BeforeValidator, Field, WrapSerializer, model_validator

from atlas.core.types.base import AtlasModel, Real, freeze, thaw

_CANONICAL_RE = re.compile(r"[A-Z][A-Z0-9]{0,9}(\.[A-Z0-9]{1,4})?")
_SEPARATORS = str.maketrans({"-": ".", "/": "."})


def normalize_symbol(raw: str) -> str:
    """Canonical form of a ticker: whitespace stripped, upper case, `-` or `/` before a share
    class turned into `.` (`" brk-b "` → `"BRK.B"`).

    Raises `ValueError` for non-ASCII input (`upper()` would turn `"straße"` into `"STRASSE"`),
    or if the result is not 1-10 letters/digits starting with a letter, optionally followed
    by `.` and a 1-4 character class suffix.
    """
    if not raw.isascii():
        raise ValueError(f"invalid symbol {raw!r}: only ASCII characters are allowed")
    symbol = raw.strip().upper().translate(_SEPARATORS)
    if not _CANONICAL_RE.fullmatch(symbol):
        raise ValueError(f"invalid symbol {raw!r}")
    return symbol


def vendor_symbol(symbol: str, separator: str = "-") -> str:
    """`symbol` (any accepted spelling) with `separator` before the share class, for a vendor
    API (`"BRK.B"` → `"BRK-B"`)."""
    return normalize_symbol(symbol).replace(".", separator)


def _symbol_before(value: object) -> object:
    return normalize_symbol(value) if isinstance(value, str) else value


def _unique_symbol_keys(value: object) -> object:
    if not isinstance(value, Mapping):
        return value
    out: dict[object, object] = {}
    for key, item in value.items():
        norm = normalize_symbol(key) if isinstance(key, str) else key
        if norm in out:
            raise ValueError(f"two keys normalise to the same symbol {norm!r}")
        out[norm] = item
    return out


Symbol = Annotated[str, BeforeValidator(_symbol_before)]
"""A ticker, normalised on input (see `normalize_symbol`)."""

SymbolWeights = Annotated[
    Mapping[Symbol, Real],
    BeforeValidator(_unique_symbol_keys),
    AfterValidator(freeze),
    WrapSerializer(thaw),
]
"""Read-only `{symbol: weight}` map; weights are fractions of NAV, shorts negative. Keys are
normalised, and two keys that normalise to the same symbol are rejected."""


class AssetClass(StrEnum):
    """Kind of instrument the fund can hold."""

    EQUITY = "EQUITY"
    ETF = "ETF"
    CASH = "CASH"


class Instrument(AtlasModel):
    """A tradable instrument. `name` is the issuer or fund name; `exchange` is the listing
    exchange (an ISO 10383 MIC such as `XNYS` when known); `sector`/`industry` are GICS names
    (`None` for ETFs and cash); `currency` is the ISO 4217 code prices are quoted in;
    `first_trade_date`/`last_trade_date` are the first and last sessions with a price in the
    lake (`None` if unknown); `is_shortable` says whether the fund may hold it short (never
    for cash)."""

    symbol: Symbol
    asset_class: AssetClass
    name: str | None = None
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] = "USD"
    first_trade_date: date | None = None
    last_trade_date: date | None = None
    is_shortable: bool = True

    @property
    def is_etf(self) -> bool:
        """Whether this is an exchange-traded fund."""
        return self.asset_class is AssetClass.ETF

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.asset_class is AssetClass.CASH and self.is_shortable:
            raise ValueError("a CASH instrument cannot be shortable")
        first, last = self.first_trade_date, self.last_trade_date
        if first is not None and last is not None and first > last:
            raise ValueError(f"first_trade_date {first} is after last_trade_date {last}")
        return self
