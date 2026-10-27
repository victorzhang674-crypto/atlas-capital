"""Curated daily prices and corporate actions: table schemas and ingestion into the lake.

`prices_daily` holds raw OHLCV plus the vendor's adjusted close, one row per symbol and
session; `corporate_actions` holds splits and cash dividends (see
`atlas.data.providers.base` for units). Both are registered in `DEFAULT_REGISTRY` when this
module is imported.

Point in time: a bar of session D is known at `bar_available_at(D)` (session close + the
configured lag, 16:15 ET or 13:15 ET on an early close). An action is known at the
`available_at` of its ex-date bar, the first price that shows it. `adj_close` is the vendor's
value at fetch time, which embeds actions after D: use raw prices plus `corporate_actions`
for anything that must not look ahead.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import PriceDataConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.pit import bar_available_at
from atlas.core.storage import (
    AVAILABLE_AT,
    DEFAULT_REGISTRY,
    YEAR,
    Column,
    ColumnType,
    Lake,
    TableSchema,
    validate_frame,
)
from atlas.core.types.base import FrozenDict
from atlas.data.providers.base import (
    PriceProvider,
    check_request,
    empty_actions,
    empty_daily,
    normalize_actions,
    normalize_daily,
)

_S, _F, _I, _D, _T = (
    ColumnType.STRING,
    ColumnType.FLOAT64,
    ColumnType.INT64,
    ColumnType.DATE,
    ColumnType.TIMESTAMP,
)

PRICES_DAILY = DEFAULT_REGISTRY.register(
    TableSchema(
        name="prices_daily",
        columns=(
            Column(name="symbol", dtype=_S),
            Column(name="date", dtype=_D),
            Column(name="open", dtype=_F),
            Column(name="high", dtype=_F),
            Column(name="low", dtype=_F),
            Column(name="close", dtype=_F),
            Column(name="adj_close", dtype=_F),
            Column(name="volume", dtype=_I),
            Column(name="vendor", dtype=_S),
            Column(name=AVAILABLE_AT, dtype=_T),
        ),
        primary_key=("symbol", "date"),
        partition_by=(YEAR,),
        year_column="date",
        description="Daily bars: raw OHLCV (USD, shares), vendor adj_close; Stage 07",
    )
)
"""`prices_daily`: one row per symbol and session, partitioned by year of `date`."""

CORPORATE_ACTIONS = DEFAULT_REGISTRY.register(
    TableSchema(
        name="corporate_actions",
        columns=(
            Column(name="symbol", dtype=_S),
            Column(name="ex_date", dtype=_D),
            Column(name="type", dtype=_S),
            Column(name="ratio", dtype=_F, nullable=True),
            Column(name="amount", dtype=_F, nullable=True),
            Column(name="vendor", dtype=_S),
            Column(name=AVAILABLE_AT, dtype=_T),
        ),
        primary_key=("symbol", "ex_date", "type"),
        description="Splits (ratio new/old) and cash dividends (raw USD per share); Stage 07",
    )
)
"""`corporate_actions`: one row per symbol, ex-date and action type."""

_log = get_logger(__name__)


@dataclass(frozen=True)
class AdjustmentMismatch:
    """A session where our recomputed adjusted close and the vendor's differ by more than the
    tolerance. `ours` and `vendor` are both rescaled to 1.0 on the symbol's last bar;
    `rel_diff = ours / vendor - 1`."""

    symbol: str
    date: date
    ours: float
    vendor: float
    rel_diff: float


@dataclass(frozen=True)
class IngestReport:
    """Outcome of `ingest_prices`.

    `rows` / `action_rows` were upserted; `symbols_ok` and `symbols_failed` keep request
    order; `failure_reasons` says why each failed symbol was skipped; `mismatches` lists
    adjustment cross-check differences above `adj_mismatch_tolerance` (recorded, not fatal).
    """

    rows: int
    action_rows: int
    symbols_ok: tuple[str, ...]
    symbols_failed: tuple[str, ...]
    failure_reasons: FrozenDict[str, str] = field(default_factory=lambda: FrozenDict({}))
    mismatches: tuple[AdjustmentMismatch, ...] = ()


def ingest_prices(
    lake: Lake,
    provider: PriceProvider,
    symbols: Sequence[str],
    start: date,
    end: date,
    *,
    calendar: TradingCalendar | None = None,
    config: PriceDataConfig | None = None,
) -> IngestReport:
    """Fetch `symbols` for `[start, end]` from `provider` and upsert them into the lake.

    A symbol is skipped, with a reason, if its prices or actions cannot be fetched (a failed
    batch is retried one symbol at a time), it has no rows, a bar falls on a non-session or
    its rows fail table validation; other symbols are still written. Re-running is
    idempotent (upsert on the primary keys; the newest fetch wins). Pass the run's
    `TradingCalendar(config=cfg.calendar)` and `cfg.data.prices`; the defaults are the
    built-in ones.
    """
    cal = calendar if calendar is not None else TradingCalendar()
    tolerance = (config if config is not None else PriceDataConfig()).adj_mismatch_tolerance
    wanted = check_request(symbols, start, end)
    failures: dict[str, str] = {}

    daily = _fetch_each(provider.fetch_daily, "prices", wanted, start, end, failures)
    daily = daily[daily["symbol"].isin(wanted)]
    bars = {str(s): g.reset_index(drop=True) for s, g in daily.groupby("symbol", sort=False)}
    for symbol in wanted:
        if symbol not in failures and symbol not in bars:
            failures[symbol] = "no price data returned"
    have = [s for s in wanted if s not in failures]

    actions = _fetch_each(provider.fetch_actions, "actions", have, start, end, failures)
    good_bars, good_actions, ok = [], [], []
    mismatches: list[AdjustmentMismatch] = []
    for symbol in have:
        if symbol in failures:
            continue
        own_actions = actions[actions["symbol"] == symbol].reset_index(drop=True)
        try:
            price_rows = _with_available_at(bars[symbol], "date", cal, symbol)
            action_rows = _with_available_at(own_actions, "ex_date", cal, symbol)
            validate_frame(PRICES_DAILY, price_rows)
            validate_frame(CORPORATE_ACTIONS, action_rows)
            found = cross_check_adjustment(bars[symbol], own_actions, tolerance)
        except DataError as exc:
            failures[symbol] = str(exc)
            continue
        if found:
            worst = max(found, key=lambda m: abs(m.rel_diff))
            _log.warning(
                "prices.adj_mismatch",
                symbol=symbol,
                sessions=len(found),
                worst_date=worst.date.isoformat(),
                worst_rel_diff=worst.rel_diff,
            )
        mismatches.extend(found)
        good_bars.append(price_rows)
        good_actions.append(action_rows)
        ok.append(symbol)

    rows = _upsert(lake, PRICES_DAILY, good_bars)
    action_count = _upsert(lake, CORPORATE_ACTIONS, good_actions)
    failed = tuple(s for s in wanted if s in failures)
    for symbol in failed:
        _log.warning("prices.symbol_failed", symbol=symbol, reason=failures[symbol])
    report = IngestReport(
        rows=rows,
        action_rows=action_count,
        symbols_ok=tuple(ok),
        symbols_failed=failed,
        failure_reasons=FrozenDict({s: failures[s] for s in failed}),
        mismatches=tuple(mismatches),
    )
    _log.info(
        "prices.ingest",
        vendor=provider.name,
        start=start.isoformat(),
        end=end.isoformat(),
        rows=report.rows,
        action_rows=report.action_rows,
        symbols_ok=len(report.symbols_ok),
        symbols_failed=len(report.symbols_failed),
        mismatches=len(report.mismatches),
    )
    return report


def adjustment_factors(daily: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
    """`daily` (same rows, same order) plus our own backward adjustment, per symbol:

    - `split_factor` = product over splits with ex-date after the row's date of `1 / ratio`;
    - `total_factor` = `split_factor` times, for each dividend with ex-date after the row's
      date, `1 - amount / prev_close` (CRSP style). `prev_close` is the raw close of the last
      bar before the ex-date, restated per new share when a split shares the ex-date; a
      dividend with no earlier bar in `daily` changes no row;
    - `split_adj_close` = `close * split_factor`, `calc_adj_close` = `close * total_factor`.

    Factors are relative to the last bar of `daily`; actions after it are not known here.
    A dividend at or above the prior close raises `DataError`.
    """
    out = daily.copy()
    split = np.ones(len(daily))
    total = np.ones(len(daily))
    days = daily["date"].to_numpy()
    closes = daily["close"].to_numpy(dtype="float64")
    for symbol, rows in daily.groupby("symbol", sort=False).indices.items():
        own = actions[actions["symbol"] == symbol]
        splits = own[own["type"] == "split"]
        for ex_date, ratio in zip(splits["ex_date"], splits["ratio"], strict=True):
            before = rows[days[rows] < ex_date]
            split[before] /= ratio
            total[before] /= ratio
        dividends = own[own["type"] == "dividend"]
        for ex_date, amount in zip(dividends["ex_date"], dividends["amount"], strict=True):
            before = rows[days[rows] < ex_date]
            if len(before) == 0:
                continue
            last = before[np.argmax(days[before])]
            same_day = splits.loc[splits["ex_date"] == ex_date, "ratio"].prod()
            prev_close = closes[last] / same_day
            if not 0 <= amount < prev_close:
                raise DataError(
                    f"{symbol}: dividend {amount} on {ex_date} is not below the prior close "
                    f"{prev_close:.4f}"
                )
            total[before] *= 1 - amount / prev_close
    out["split_factor"] = split
    out["total_factor"] = total
    out["split_adj_close"] = out["close"] * split
    out["calc_adj_close"] = out["close"] * total
    return out


def cross_check_adjustment(
    daily: pd.DataFrame, actions: pd.DataFrame, tolerance: float
) -> tuple[AdjustmentMismatch, ...]:
    """Sessions where our adjusted close and the vendor's `adj_close` disagree.

    Both series are rescaled to 1.0 on each symbol's last bar (the vendor's also carries
    actions after the window), then `rel_diff = ours / vendor - 1`; rows with
    `|rel_diff| > tolerance` (a fraction: 0.005 = 0.5%) are returned in date order per symbol.
    """
    adj = adjustment_factors(daily, actions)
    found: list[AdjustmentMismatch] = []
    for symbol, group in adj.groupby("symbol", sort=True):
        group = group.sort_values("date", kind="stable")
        ours_last, vendor_last = group["calc_adj_close"].iloc[-1], group["adj_close"].iloc[-1]
        if not (ours_last > 0 and vendor_last > 0):
            raise DataError(f"{symbol}: cannot cross-check adjustment with a non-positive close")
        ours = group["calc_adj_close"] / ours_last
        vendor = group["adj_close"] / vendor_last
        rel = ours / vendor - 1
        for day, o, v, r in zip(group["date"], ours, vendor, rel, strict=True):
            if abs(r) > tolerance:
                found.append(AdjustmentMismatch(str(symbol), day, float(o), float(v), float(r)))
    return tuple(found)


def _fetch_each(
    fetch: Callable[[Sequence[str], date, date], pd.DataFrame],
    kind: Literal["prices", "actions"],
    symbols: list[str] | tuple[str, ...],
    start: date,
    end: date,
    failures: dict[str, str],
) -> pd.DataFrame:
    """`fetch` for all `symbols`; if the batch raises `DataError`, one symbol at a time,
    recording each symbol that still fails in `failures`."""
    normalize, empty = (
        (normalize_daily, empty_daily())
        if kind == "prices"
        else (normalize_actions, empty_actions())
    )
    if not symbols:
        return empty
    try:
        return normalize(fetch(list(symbols), start, end))
    except DataError as exc:
        _log.warning("prices.batch_failed", kind=kind, symbols=len(symbols), error=str(exc))
    frames = []
    for symbol in symbols:
        try:
            frames.append(normalize(fetch([symbol], start, end)))
        except DataError as exc:
            failures[symbol] = f"{kind} fetch failed: {exc}"
    frames = [f for f in frames if not f.empty]
    return normalize(pd.concat(frames, ignore_index=True)) if frames else empty


def _with_available_at(
    frame: pd.DataFrame, day_col: str, cal: TradingCalendar, symbol: str
) -> pd.DataFrame:
    """`frame` plus `available_at`: the bar availability of each row's session (for an
    action, the first session on or after its ex-date). A bar on a non-session or outside
    the calendar raises `DataError`."""
    stamps: dict[date, datetime] = {}
    for day in dict.fromkeys(frame[day_col]):
        try:
            if day_col == "date" and not cal.is_session(day):
                raise DataError(f"{symbol}: bar dated {day.isoformat()} is not a trading session")
            session = day if cal.is_session(day) else cal.next_session(day)
            stamps[day] = bar_available_at(session, cal)
        except ValueError as exc:
            raise DataError(f"{symbol}: {exc}") from exc
    out = frame.copy()
    values = [stamps[d] for d in frame[day_col]]
    out[AVAILABLE_AT] = pd.to_datetime(pd.Series(values, dtype="object"), utc=True).dt.as_unit("us")
    return out


def _upsert(lake: Lake, schema: TableSchema, frames: list[pd.DataFrame]) -> int:
    frames = [f for f in frames if not f.empty]
    if not frames:
        return 0
    batch = pd.concat(frames, ignore_index=True)
    return lake.write_table(schema.name, batch, mode="upsert").rows
