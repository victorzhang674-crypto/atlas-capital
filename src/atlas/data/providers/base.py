"""The price provider contract: what every daily-bar vendor adapter returns, plus shared
request checks, retry and rate-limit helpers.

Daily frame (`DAILY_COLUMNS`), one row per symbol and session:

| Column | Type | Meaning |
|---|---|---|
| `symbol` | `str` | canonical ticker (`BRK.B`) |
| `date` | `datetime.date` | session date (New York) |
| `open`, `high`, `low`, `close` | `float64` | **raw** traded prices, USD (not split-adjusted) |
| `adj_close` | `float64` | the vendor's split- and dividend-adjusted close as of fetch time, USD |
| `volume` | `int64` | raw shares traded |
| `vendor` | `str` | provider name that served the row |

Actions frame (`ACTION_COLUMNS`), one row per corporate action:

| Column | Type | Meaning |
|---|---|---|
| `symbol` | `str` | canonical ticker |
| `ex_date` | `datetime.date` | ex-date |
| `type` | `str` | `split` or `dividend` |
| `ratio` | `float64` (NaN for dividends) | new shares per old share (4.0 for 4:1, 0.1 for 1:10) |
| `amount` | `float64` (NaN for splits) | raw cash per share on the ex-date, USD |
| `vendor` | `str` | provider name |

Both fetch methods include `start` and `end`. Providers raise `DataError` when the vendor
fails (after retries) and simply leave out symbols they have no data for (a delisted name).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Literal, Protocol, Self, get_args, runtime_checkable

import pandas as pd

from atlas.core.config import PriceDataConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.types.instruments import normalize_symbol

DAILY_COLUMNS: Final = (
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "adj_close",
    "volume",
    "vendor",
)
"""Columns of a daily price frame, in order."""

ACTION_COLUMNS: Final = ("symbol", "ex_date", "type", "ratio", "amount", "vendor")
"""Columns of a corporate actions frame, in order."""

ActionType = Literal["split", "dividend"]
ACTION_TYPES: Final = frozenset(get_args(ActionType))

_PRICE_COLUMNS = ("open", "high", "low", "close", "adj_close")
_NUMERIC_KINDS = frozenset({"floating", "integer", "mixed-integer-float", "decimal", "empty"})

_log = get_logger(__name__)


@runtime_checkable
class PriceProvider(Protocol):
    """A source of daily bars and corporate actions (see the module docstring for frames)."""

    name: str

    def fetch_daily(self, symbols: Sequence[str], start: date, end: date) -> pd.DataFrame:
        """Daily bars of `symbols` for sessions in `[start, end]`."""
        ...

    def fetch_actions(self, symbols: Sequence[str], start: date, end: date) -> pd.DataFrame:
        """Splits and dividends of `symbols` with ex-dates in `[start, end]`."""
        ...


@runtime_checkable
class ServedRecorder(Protocol):
    """A provider that remembers which vendor served each symbol's bars (to route actions)."""

    def record_served(self, daily: pd.DataFrame) -> None:
        """Note the `vendor` of each symbol in `daily`, bars served without this provider."""
        ...


def empty_daily() -> pd.DataFrame:
    """A daily frame with no rows and the contract's dtypes."""
    return normalize_daily(pd.DataFrame({c: [] for c in DAILY_COLUMNS}))


def empty_actions() -> pd.DataFrame:
    """An actions frame with no rows and the contract's dtypes."""
    return normalize_actions(pd.DataFrame({c: [] for c in ACTION_COLUMNS}))


def normalize_daily(df: pd.DataFrame) -> pd.DataFrame:
    """`df` in the daily contract: exactly `DAILY_COLUMNS` (extra columns dropped), dtypes
    set, sorted by symbol then date, fresh index.

    Values are not judged here (NaN prices stay NaN, so validation can reject the symbol);
    `volume` becomes nullable `Int64` if it holds missing values. A missing column, a
    non-numeric price/volume column, a non-integral volume or a bad date raises `DataError`.
    """
    _require_columns(df, DAILY_COLUMNS, "daily prices")
    out = pd.DataFrame(
        {
            "symbol": _strings(df["symbol"]),
            "date": _dates(df["date"], "date"),
            **{c: _floats(df[c], c) for c in _PRICE_COLUMNS},
            "volume": _volume(df["volume"]),
            "vendor": _strings(df["vendor"]),
        }
    )
    return out.sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)


def normalize_actions(df: pd.DataFrame) -> pd.DataFrame:
    """`df` in the actions contract: exactly `ACTION_COLUMNS`, dtypes set, sorted by symbol,
    ex-date and type, fresh index. An unknown action type raises `DataError`."""
    _require_columns(df, ACTION_COLUMNS, "corporate actions")
    types = _strings(df["type"])
    unknown = sorted(set(types) - ACTION_TYPES)
    if unknown:
        raise DataError(f"corporate actions: unknown action types {unknown}")
    out = pd.DataFrame(
        {
            "symbol": _strings(df["symbol"]),
            "ex_date": _dates(df["ex_date"], "ex_date"),
            "type": types,
            "ratio": _floats(df["ratio"], "ratio"),
            "amount": _floats(df["amount"], "amount"),
            "vendor": _strings(df["vendor"]),
        }
    )
    return out.sort_values(["symbol", "ex_date", "type"], kind="stable").reset_index(drop=True)


def check_request(symbols: Sequence[str], start: date, end: date) -> tuple[str, ...]:
    """Canonical symbols of a request, de-duplicated in first-seen order.

    `ValueError` for a bare string instead of a list, an invalid symbol, a `datetime` where a
    trading `date` belongs, or `start > end`.
    """
    if isinstance(symbols, str):
        raise ValueError("symbols must be a sequence of tickers, not one string")
    for bound in (start, end):
        if isinstance(bound, datetime) or not isinstance(bound, date):
            raise ValueError(f"start and end must be dates, got {bound!r}")
    if start > end:
        raise ValueError(f"start ({start}) must not be after end ({end})")
    return tuple(dict.fromkeys(normalize_symbol(s) for s in symbols))


@dataclass(frozen=True)
class RetryPolicy:
    """Retry and rate-limit settings for vendor requests (seconds)."""

    max_retries: int = 3
    backoff_seconds: float = 1.0
    min_interval_seconds: float = 0.5

    @classmethod
    def from_config(cls, config: PriceDataConfig) -> Self:
        """The policy from `cfg.data.prices`."""
        return cls(
            max_retries=config.max_retries,
            backoff_seconds=config.backoff_seconds,
            min_interval_seconds=config.min_request_interval_seconds,
        )


def call_with_retry[T](
    fn: Callable[[], T],
    policy: RetryPolicy,
    sleep: Callable[[float], None],
    what: str,
    retry_on: tuple[type[BaseException], ...],
    *,
    event: str = "prices.request_failed",
) -> T:
    """Call `fn`, retrying up to `policy.max_retries` times on the `retry_on` exceptions.

    Waits `backoff_seconds * 2**i` before retry `i + 1`; each failure is logged as a warning
    named `event`. When every attempt failed, raises `DataError` naming `what`, chained to the
    last error. Any other exception propagates at once (a bug is not a vendor outage).
    """
    attempts = policy.max_retries + 1
    for attempt in range(attempts):
        try:
            return fn()
        except retry_on as exc:
            _log.warning(event, what=what, attempt=attempt + 1, error=str(exc))
            if attempt + 1 == attempts:
                raise DataError(f"{what} failed after {attempts} attempt(s): {exc}") from exc
            sleep(policy.backoff_seconds * 2**attempt)
    raise AssertionError("unreachable")  # pragma: no cover


class Throttle:
    """Rate limit: `wait()` pauses `min_interval` seconds before every call but the first."""

    def __init__(self, min_interval: float, sleep: Callable[[float], None]) -> None:
        """A throttle that sleeps with `sleep` (seconds)."""
        self._min_interval = min_interval
        self._sleep = sleep
        self._started = False

    def wait(self) -> None:
        """Pause before the next vendor request (no pause before the first one)."""
        if self._started and self._min_interval > 0:
            self._sleep(self._min_interval)
        self._started = True


def _require_columns(df: pd.DataFrame, columns: Sequence[str], what: str) -> None:
    if not isinstance(df, pd.DataFrame):
        raise DataError(f"{what}: expected a pandas DataFrame, got {type(df).__name__}")
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise DataError(f"{what}: missing columns {missing}")


def _strings(series: pd.Series) -> pd.Series:
    return series.astype("str").reset_index(drop=True)


def _floats(series: pd.Series, name: str) -> pd.Series:
    kind = pd.api.types.infer_dtype(series, skipna=True)
    if kind not in _NUMERIC_KINDS or pd.api.types.is_bool_dtype(series.dtype):
        raise DataError(f"column {name!r}: expected numbers, got {kind} ({series.dtype})")
    return pd.to_numeric(series).astype("float64").reset_index(drop=True)


def _volume(series: pd.Series) -> pd.Series:
    values = _floats(series, "volume")
    present = values.dropna()
    if not (present == present.round()).all():
        raise DataError("column 'volume': share volumes must be whole numbers")
    if values.isna().any():
        return values.astype("Int64")
    return values.astype("int64")


def _dates(series: pd.Series, name: str) -> pd.Series:
    if isinstance(series.dtype, pd.DatetimeTZDtype):
        raise DataError(f"column {name!r}: trading dates must be naive, got {series.dtype}")
    if pd.api.types.is_datetime64_dtype(series.dtype):
        return pd.Series([ts.date() for ts in series], dtype="object")
    out = []
    for value in series:
        if isinstance(value, datetime):
            out.append(value.date())
        elif isinstance(value, date):
            out.append(value)
        else:
            raise DataError(f"column {name!r}: expected dates, got {value!r}")
    return pd.Series(out, dtype="object")
