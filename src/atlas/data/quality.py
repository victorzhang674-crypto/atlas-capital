"""Data quality checks, the daily quality report and the persisted `data_quality_issues` table.

Each check is a frame-level function returning `QualityIssue`s. `quality_report(lake, as_of)`
runs every check on the data **known at `as_of`** (`pit_filter`, then `assert_no_lookahead`)
over a window of the last `data.quality.window_sessions` sessions whose bars are known, and
can persist the result. A `critical` issue blocks its symbol (or macro series) for that day:
`DataView` leaves it out (`blocked_symbols`).

Checks (severities from `data.quality.severity`):

- `missing_sessions` (`prices_daily`): a session with no bar between a symbol's first and
  last bar in the window;
- `stale_price` (`prices_daily`): `stale_min_sessions` consecutive unchanged closes, each on
  a bar with volume;
- `return_outlier` (`prices_daily`): an adjusted return beyond `outlier_sigma` sigma on a
  session without a corporate action;
- `ohlc_inconsistent` (`prices_daily`): a high or low not bounding the bar, a missing OHLC
  value or a negative volume;
- `nonpositive_price` (`prices_daily`): an open, high, low or close at or below zero;
- `split_mismatch` (`corporate_actions`): a recorded action that does not explain the move,
  an action the adjustment rejects, or a move matching an unrecorded split;
- `fundamentals_jump` (`fundamentals_facts`): a quarterly value changing more than
  `fundamentals_jump_ratio` times;
- `macro_gap` (`macro_observations`): a gap between observations, or no recent one;
- `vendor_mix` (`prices_daily`): bars from more than one vendor in the window.

Returns are simple daily returns of the close adjusted (Stage 07 `adjustment_factors`) with the
corporate actions known at `as_of`; the vendor's `adj_close` is never used (it is the vendor's
value at each bar's fetch time, so stored values from different fetches disagree).
"""

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from itertools import pairwise
from typing import Final

import numpy as np
import pandas as pd

from atlas.core.calendar import MARKET_TZ, TradingCalendar, market_date
from atlas.core.config import AtlasConfig, FrequencyDays, FundamentalsConfig, QualityConfig
from atlas.core.logging import get_logger
from atlas.core.pit import AsOf, assert_no_lookahead, bar_available_at, pit_filter
from atlas.core.storage import (
    AVAILABLE_AT,
    DEFAULT_REGISTRY,
    YEAR,
    Column,
    ColumnType,
    Lake,
    TableSchema,
)
from atlas.data.fundamentals import (
    FUNDAMENTALS_FACTS,
    ConceptMap,
    load_concept_map,
    resolve_concept,
)
from atlas.data.fundamentals.pit import known_facts, quarterly_values
from atlas.data.macro.pit import known_observations
from atlas.data.macro.series import MACRO_OBSERVATIONS
from atlas.data.prices import CORPORATE_ACTIONS, PRICES_DAILY, adjustment_factors

CHECKS: Final = (
    "missing_sessions",
    "stale_price",
    "return_outlier",
    "ohlc_inconsistent",
    "nonpositive_price",
    "split_mismatch",
    "fundamentals_jump",
    "macro_gap",
    "vendor_mix",
    "fundamentals_period_after_filing",
)
"""Names of the quality checks, as used in `QualityIssue.check` and `data.quality.severity`."""

_RANK: Final = {"info": 0, "warning": 1, "critical": 2}


class Severity(StrEnum):
    """How bad an issue is. Ordered: `INFO < WARNING < CRITICAL`; only `CRITICAL` blocks."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Severity):
            return NotImplemented
        return _RANK[self.value] < _RANK[other.value]

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Severity):
            return NotImplemented
        return _RANK[self.value] <= _RANK[other.value]

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Severity):
            return NotImplemented
        return _RANK[self.value] > _RANK[other.value]

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Severity):
            return NotImplemented
        return _RANK[self.value] >= _RANK[other.value]


@dataclass(frozen=True)
class QualityIssue:
    """One data quality finding.

    `table` is the lake table the data came from; `symbol` the ticker (for a macro check the
    FRED series id); `date` the day the issue is about (a bar's session; for fundamentals the
    New York date the filing became public; for macro the observation after a gap, or the
    as-of day for a series with no recent observation); `check` one of `CHECKS`; `detail` a
    human-readable explanation; `available_at` (aware, UTC) when the issue became knowable:
    the latest `available_at` of the rows it is based on.
    """

    table: str
    symbol: str
    date: date
    check: str
    severity: Severity
    detail: str
    available_at: datetime

    def __post_init__(self) -> None:
        if self.check not in CHECKS:
            raise ValueError(f"unknown check {self.check!r}; known: {', '.join(CHECKS)}")
        ts = self.available_at
        if ts.tzinfo is None or ts.utcoffset() is None:
            raise ValueError(f"naive available_at {ts!r}; pass a timezone-aware datetime")


ISSUE_COLUMNS: Final = ("table", "symbol", "date", "check", "severity", "detail", AVAILABLE_AT)
"""Columns of `issues_frame` and of `quality_report` (in order)."""

_S, _D, _T = ColumnType.STRING, ColumnType.DATE, ColumnType.TIMESTAMP

DATA_QUALITY_ISSUES = DEFAULT_REGISTRY.register(
    TableSchema(
        name="data_quality_issues",
        columns=(
            Column(name="report_day", dtype=_D),
            Column(name="table", dtype=_S),
            Column(name="symbol", dtype=_S),
            Column(name="date", dtype=_D),
            Column(name="check", dtype=_S),
            Column(name="severity", dtype=_S),
            Column(name="detail", dtype=_S),
            Column(name=AVAILABLE_AT, dtype=_T),
        ),
        primary_key=("report_day", "table", "symbol", "check", "date"),
        partition_by=(YEAR,),
        year_column="report_day",
        description="Data quality issues per daily report (report_day = as-of day); Stage 13",
    )
)
"""`data_quality_issues`: the issues of each persisted `quality_report`, by report day."""


def _stamps(values: Sequence[object]) -> pd.Series:
    return pd.to_datetime(pd.Series(list(values), dtype="object"), utc=True).astype(
        "datetime64[us, UTC]"
    )


def issues_frame(issues: Sequence[QualityIssue]) -> pd.DataFrame:
    """`issues` as a frame with `ISSUE_COLUMNS` (`severity` as its string value, `date` as
    `datetime.date`, `available_at` as `datetime64[us, UTC]`), in the given order."""
    frame = pd.DataFrame(
        {
            "table": pd.Series([i.table for i in issues], dtype="object"),
            "symbol": pd.Series([i.symbol for i in issues], dtype="object"),
            "date": pd.Series([i.date for i in issues], dtype="object"),
            "check": pd.Series([i.check for i in issues], dtype="object"),
            "severity": pd.Series([i.severity.value for i in issues], dtype="object"),
            "detail": pd.Series([i.detail for i in issues], dtype="object"),
            AVAILABLE_AT: _stamps([i.available_at for i in issues]),
        }
    )
    return frame[list(ISSUE_COLUMNS)]


# --- helpers -------------------------------------------------------------------------------------

_PRICES = "prices_daily"
_OHLC = ("open", "high", "low", "close")
_EPS = 1e-9
"""Relative tolerance of OHLC comparisons (floating-point noise, not vendor rounding)."""


def _issue(
    table: str,
    symbol: object,
    day: object,
    check: str,
    severity: Severity,
    detail: str,
    available_at: object,
) -> QualityIssue:
    ts = pd.Timestamp(available_at)  # type: ignore[arg-type]
    return QualityIssue(
        table=table,
        symbol=str(symbol),
        date=day if isinstance(day, date) else pd.Timestamp(day).date(),  # type: ignore[arg-type]
        check=check,
        severity=severity,
        detail=detail,
        available_at=ts.to_pydatetime(),
    )


def _sorted(bars: pd.DataFrame) -> pd.DataFrame:
    return bars.sort_values(["symbol", "date"], kind="stable", ignore_index=True)


def _in_window(bars: pd.DataFrame, since: date) -> pd.DataFrame:
    return _sorted(bars[(bars["date"] >= since).to_numpy(dtype=bool)])


def _fmt(value: float) -> str:
    return f"{value:.6g}"


# --- price checks --------------------------------------------------------------------------------


def check_missing_sessions(
    bars: pd.DataFrame, calendar: TradingCalendar, *, since: date, severity: Severity
) -> list[QualityIssue]:
    """Sessions on or after `since` with no bar, between each symbol's first and last bar on
    or after `since` (`prices_daily` rows). One issue per missing session; its `available_at`
    is that of the next bar (the bar that shows the gap)."""
    issues = []
    for symbol, group in _in_window(bars, since).groupby("symbol", sort=True):
        days = list(group["date"])
        expected = calendar.sessions(days[0], days[-1])
        if len(expected) == len(days):
            continue
        present = set(days)
        stamps = group[AVAILABLE_AT].to_list()
        for missing in expected:
            if missing in present:
                continue
            nxt = bisect_left(days, missing)
            issues.append(
                _issue(
                    _PRICES,
                    symbol,
                    missing,
                    "missing_sessions",
                    severity,
                    f"no bar on session {missing.isoformat()}",
                    stamps[nxt],
                )
            )
    return issues


def check_ohlc(bars: pd.DataFrame, *, since: date, severity: Severity) -> list[QualityIssue]:
    """Bars on or after `since` with a missing open/high/low/close, a high below the open,
    close or low, a low above the open, close or high, or a negative volume."""
    frame = _in_window(bars, since)
    if frame.empty:
        return []
    o, h, lo, c = (frame[col].to_numpy(dtype="float64") for col in _OHLC)
    tol = _EPS * np.nanmax(np.abs(np.vstack([o, h, lo, c])), axis=0, initial=0.0)
    missing = np.isnan(np.vstack([o, h, lo, c])).any(axis=0)
    with np.errstate(invalid="ignore"):
        high_bad = h < np.fmax(np.fmax(o, c), lo) - tol
        low_bad = lo > np.fmin(np.fmin(o, c), h) + tol
    vol_bad = frame["volume"].to_numpy() < 0
    issues = []
    for i in np.flatnonzero(missing | high_bad | low_bad | vol_bad):
        reasons = []
        if missing[i]:
            reasons.append("missing OHLC value")
        if high_bad[i]:
            reasons.append(f"high {_fmt(h[i])} below open/close/low")
        if low_bad[i]:
            reasons.append(f"low {_fmt(lo[i])} above open/close/high")
        if vol_bad[i]:
            reasons.append(f"negative volume {int(frame['volume'].iloc[i])}")
        row = frame.iloc[i]
        issues.append(
            _issue(
                _PRICES,
                row["symbol"],
                row["date"],
                "ohlc_inconsistent",
                severity,
                "; ".join(reasons),
                row[AVAILABLE_AT],
            )
        )
    return issues


def check_nonpositive_prices(
    bars: pd.DataFrame, *, since: date, severity: Severity
) -> list[QualityIssue]:
    """Bars on or after `since` with an open, high, low or close at or below zero."""
    frame = _in_window(bars, since)
    if frame.empty:
        return []
    values = frame[list(_OHLC)].to_numpy(dtype="float64")
    bad = values <= 0
    issues = []
    for i in np.flatnonzero(bad.any(axis=1)):
        cols = [f"{col} {_fmt(values[i, j])}" for j, col in enumerate(_OHLC) if bad[i, j]]
        row = frame.iloc[i]
        issues.append(
            _issue(
                _PRICES,
                row["symbol"],
                row["date"],
                "nonpositive_price",
                severity,
                "non-positive " + ", ".join(cols),
                row[AVAILABLE_AT],
            )
        )
    return issues


def check_stale_prices(
    bars: pd.DataFrame, *, since: date, min_sessions: int, severity: Severity
) -> list[QualityIssue]:
    """Runs of at least `min_sessions` consecutive bars whose close equals the previous bar's
    close, each with volume > 0 (a frozen feed: trading but no price change). One issue per
    run, dated on its last bar, if that bar is on or after `since`."""
    frame = _sorted(bars)
    if frame.empty:
        return []
    same_symbol = frame["symbol"].eq(frame["symbol"].shift())
    unchanged = (
        same_symbol & frame["close"].eq(frame["close"].shift()) & frame["volume"].gt(0)
    ).to_numpy()
    run_id = np.cumsum(~unchanged)
    lengths = pd.Series(unchanged.astype(int)).groupby(run_id).transform("sum").to_numpy()
    is_last = unchanged & (np.append(run_id[1:], -1) != run_id)
    issues = []
    for i in np.flatnonzero(is_last & (lengths >= min_sessions)):
        row = frame.iloc[i]
        if row["date"] < since:
            continue
        issues.append(
            _issue(
                _PRICES,
                row["symbol"],
                row["date"],
                "stale_price",
                severity,
                f"close {_fmt(row['close'])} unchanged for {lengths[i]} sessions with volume",
                row[AVAILABLE_AT],
            )
        )
    return issues


def check_vendor_mix(bars: pd.DataFrame, *, since: date, severity: Severity) -> list[QualityIssue]:
    """Symbols whose bars on or after `since` come from more than one vendor (volumes and
    prices are not comparable across vendors). One issue per symbol, dated on the last bar
    whose vendor differs from the previous bar's."""
    issues = []
    for symbol, group in _in_window(bars, since).groupby("symbol", sort=True):
        vendors = group["vendor"].to_list()
        if len(set(vendors)) < 2:
            continue
        switches = [i for i in range(1, len(vendors)) if vendors[i] != vendors[i - 1]]
        row = group.iloc[switches[-1]]
        issues.append(
            _issue(
                _PRICES,
                symbol,
                row["date"],
                "vendor_mix",
                severity,
                "bars from vendors " + ", ".join(sorted(set(vendors))),
                row[AVAILABLE_AT],
            )
        )
    return issues


# --- return outliers and split mismatches --------------------------------------------------------

_ACTIONS = "corporate_actions"


def usable_actions(
    bars: pd.DataFrame, actions: pd.DataFrame
) -> tuple[pd.DataFrame, list[tuple[date, str]]]:
    """`actions` of one symbol without the dividends `adjustment_factors` would reject (an
    amount negative or not below the prior close, USD per share), plus `(session, reason)` per
    rejected one (`session`: its first bar on or after the ex-date). `bars` are the symbol's
    bars sorted by date."""
    days = list(bars["date"])
    closes = bars["close"].to_numpy(dtype="float64")
    splits = actions[actions["type"] == "split"]
    keep = np.ones(len(actions), dtype=bool)
    rejected = []
    for pos, (_, act) in enumerate(actions.iterrows()):
        if act["type"] != "dividend":
            continue
        before = bisect_left(days, act["ex_date"])
        if before == 0 or before == len(days):
            continue  # no bar before it (changes no row) or no bar on/after it (not in range)
        same_day = float(splits.loc[splits["ex_date"] == act["ex_date"], "ratio"].prod())
        prev_close = closes[before - 1] / same_day
        if not 0 <= act["amount"] < prev_close:
            keep[pos] = False
            rejected.append(
                (
                    days[before],
                    f"dividend {_fmt(act['amount'])} ex {act['ex_date'].isoformat()} is not "
                    f"below the prior close {_fmt(prev_close)}",
                )
            )
    return actions[keep], rejected


def _match_split(gross: float, ratios: Sequence[float], tolerance: float) -> str | None:
    """The split a gross move `gross` (close / previous close) matches, if any."""
    for k in ratios:
        if abs(gross * k - 1) <= tolerance:
            return f"{_fmt(k)}-for-1 split"
        if abs(gross / k - 1) <= tolerance:
            return f"1-for-{_fmt(k)} reverse split"
    return None


def check_return_outliers(
    bars: pd.DataFrame, actions: pd.DataFrame, *, since: date, config: QualityConfig
) -> list[QualityIssue]:
    """`return_outlier` and `split_mismatch` issues on sessions on or after `since`.

    Per symbol, closes are adjusted with `actions` (`adjustment_factors`; pass only the
    actions known at the as-of). A return is an outlier when its magnitude exceeds
    `config.outlier_sigma` times the standard deviation of the previous
    `config.outlier_vol_window` adjusted returns (at least `config.outlier_min_returns`),
    floored at `config.outlier_min_daily_vol`. On a session without an action an outlier is a
    `return_outlier`, unless its gross move is within `config.split_ratio_tolerance` of a split
    ratio in `config.split_ratios` (an unrecorded split: `split_mismatch`). On the first
    session on or after an action's ex-date, an adjusted outlier is a `split_mismatch` when the
    session has a split or the raw return is not an outlier (the action does not explain the
    move); a real move on an ex-dividend day is not reported. A dividend the adjustment would
    reject is a `split_mismatch` on its session and is left out of the adjustment.
    """
    outlier_sev = Severity(config.severity.return_outlier)
    split_sev = Severity(config.severity.split_mismatch)
    by_symbol = {str(s): g for s, g in actions.groupby("symbol", sort=False)}
    issues: list[QualityIssue] = []
    for symbol, group in _sorted(bars).groupby("symbol", sort=True):
        group = group.reset_index(drop=True)
        days = list(group["date"])
        stamps = group[AVAILABLE_AT].to_list()
        own, rejected = usable_actions(group, by_symbol.get(str(symbol), actions.iloc[0:0]))
        for day, reason in rejected:
            if day >= since:
                stamp = stamps[days.index(day)]
                issues.append(
                    _issue(_ACTIONS, symbol, day, "split_mismatch", split_sev, reason, stamp)
                )
        if len(group) < 2:
            continue
        adj = adjustment_factors(group, own)
        closes = adj["close"].to_numpy(dtype="float64")
        adjusted = adj["calc_adj_close"].to_numpy(dtype="float64")
        with np.errstate(divide="ignore", invalid="ignore"):
            raw_r = closes[1:] / closes[:-1] - 1
            adj_r = adjusted[1:] / adjusted[:-1] - 1
        sd = (
            pd.Series(adj_r)
            .rolling(config.outlier_vol_window, min_periods=config.outlier_min_returns)
            .std()
            .shift(1)
            .to_numpy()
        )
        limit = config.outlier_sigma * np.maximum(sd, config.outlier_min_daily_vol)
        with np.errstate(invalid="ignore"):
            adj_out = np.abs(adj_r) > limit
            raw_out = np.abs(raw_r) > limit
        sessions = days[1:]
        action_day = {
            days[j] for e in own["ex_date"] if 0 < (j := bisect_left(days, e)) < len(days)
        }
        split_day = {
            days[j]
            for e in own.loc[own["type"] == "split", "ex_date"]
            if 0 < (j := bisect_left(days, e)) < len(days)
        }
        for i in np.flatnonzero(adj_out):
            day = sessions[i]
            if day < since:
                continue
            sigmas = abs(adj_r[i]) / (limit[i] / config.outlier_sigma)
            size = f"return {adj_r[i]:+.2%} is {sigmas:.1f} sigma"
            if day in action_day:
                if day in split_day or not raw_out[i]:
                    issues.append(
                        _issue(
                            _ACTIONS,
                            symbol,
                            day,
                            "split_mismatch",
                            split_sev,
                            f"adjusted {size}: the recorded action does not explain the move",
                            stamps[i + 1],
                        )
                    )
                continue
            split = _match_split(1 + adj_r[i], config.split_ratios, config.split_ratio_tolerance)
            if split is not None:
                issues.append(
                    _issue(
                        _ACTIONS,
                        symbol,
                        day,
                        "split_mismatch",
                        split_sev,
                        f"{size} with no recorded action: matches an unrecorded {split}",
                        stamps[i + 1],
                    )
                )
            else:
                issues.append(
                    _issue(
                        _PRICES,
                        symbol,
                        day,
                        "return_outlier",
                        outlier_sev,
                        f"{size} with no corporate action",
                        stamps[i + 1],
                    )
                )
    return sorted(issues, key=lambda i: (i.symbol, i.date, i.check))


# --- fundamentals jumps --------------------------------------------------------------------------

_FACTS = "fundamentals_facts"


def _known_since(facts: pd.DataFrame, since: date) -> pd.DataFrame:
    """The rows of `facts` whose `available_at` falls on or after `since` in New York (at or
    after 00:00 New York that day)."""
    start = pd.Timestamp(datetime.combine(since, time(0), tzinfo=MARKET_TZ))
    return facts[(facts[AVAILABLE_AT] >= start).to_numpy(dtype=bool)]


def check_fundamentals_jumps(
    facts: pd.DataFrame,
    as_of: AsOf,
    *,
    since: date,
    concept_map: ConceptMap,
    config: FundamentalsConfig,
    jump_ratio: float,
    severity: Severity,
) -> list[QualityIssue]:
    """Quarter-over-quarter jumps of more than `jump_ratio` times (either way) in the values of
    each mapped concept known at `as_of` (`fundamentals_facts` rows of any symbols).

    Flows are compared quarter by quarter (`quarterly_values`), stocks balance by balance; two
    values are consecutive when their period ends are `config.quarter_days` apart. Both must be
    non-zero (a sign change counts by magnitude). A jump is reported only if it became known on
    or after `since` (New York date of the later value's `available_at`), so only concepts a
    fact known since then reports are resolved; one issue per symbol and day, its detail
    listing every jumped concept.
    """
    low, high = config.quarter_days
    found: dict[tuple[str, date], tuple[list[str], pd.Timestamp]] = {}
    for symbol, own in facts.groupby("symbol", sort=True):
        known = known_facts(own, as_of)
        new = _known_since(known, since)
        new_tags = set(zip(new["taxonomy"], new["tag"], strict=True))
        for concept in concept_map.values():
            if not concept.tags() & new_tags:
                continue  # no filing in the window reported this concept
            periods = resolve_concept(known, concept)
            if concept.kind == "flow":
                values = quarterly_values(periods, config)
            else:
                values = periods[periods["duration_days"] == 0]
            values = values.sort_values("period_end", kind="stable").reset_index(drop=True)
            ends, vals = list(values["period_end"]), values["value"].to_numpy(dtype="float64")
            stamps = list(values[AVAILABLE_AT])
            for i in range(1, len(values)):
                if not low <= (ends[i] - ends[i - 1]).days <= high:
                    continue
                prev, cur = abs(vals[i - 1]), abs(vals[i])
                if prev == 0 or cur == 0 or not np.isfinite(prev * cur):
                    continue
                factor = max(cur / prev, prev / cur)
                if factor <= jump_ratio:
                    continue
                stamp = max(stamps[i], stamps[i - 1])
                day = market_date(stamp.to_pydatetime())
                if day < since:
                    continue
                text = (
                    f"{concept.name}: {_fmt(vals[i - 1])} (to {ends[i - 1].isoformat()}) -> "
                    f"{_fmt(vals[i])} (to {ends[i].isoformat()}), {factor:.1f}x"
                )
                details, latest = found.get((str(symbol), day), ([], stamp))
                found[(str(symbol), day)] = ([*details, text], max(latest, stamp))
    return [
        _issue(_FACTS, symbol, day, "fundamentals_jump", severity, "; ".join(texts), stamp)
        for (symbol, day), (texts, stamp) in sorted(found.items())
    ]


def check_fundamentals_period_after_filing(
    facts: pd.DataFrame, as_of: AsOf, *, since: date, severity: Severity
) -> list[QualityIssue]:
    """Facts known at `as_of` whose period ends after their own filing date, a filer error
    (`known_facts` leaves such facts out). Reported when their filing became known on or after
    `since` (New York date of `available_at`); one issue per symbol and filing day, listing
    each fact as `tag period_end`."""
    new = _known_since(pit_filter(facts, as_of), since)
    bad = new[(new["period_end"] > new["filed"]).to_numpy(dtype=bool)]
    issues = []
    for (symbol, filed), rows in bad.groupby(["symbol", "filed"], sort=True):
        listed = sorted(
            {f"{t} {e.isoformat()}" for t, e in zip(rows["tag"], rows["period_end"], strict=True)}
        )
        detail = f"period ends after the filing of {filed}: " + ", ".join(listed)
        stamp = rows[AVAILABLE_AT].max()
        issues.append(
            _issue(
                _FACTS, symbol, filed, "fundamentals_period_after_filing", severity, detail, stamp
            )
        )
    return issues


# --- macro gaps ----------------------------------------------------------------------------------

_MACRO = "macro_observations"
_FREQUENCIES: Final = (("daily", 4), ("weekly", 10), ("monthly", 45), ("quarterly", 135))
"""Inferred frequency: the first whose bound (calendar days) the median spacing does not
exceed; above the last, `annual`."""


def infer_frequency(obs_dates: Sequence[date]) -> str | None:
    """`daily`, `weekly`, `monthly`, `quarterly` or `annual` from the median spacing (calendar
    days) of the distinct observation dates; `None` with fewer than three dates."""
    days = sorted(set(obs_dates))
    if len(days) < 3:
        return None
    spacing = float(np.median([(b - a).days for a, b in pairwise(days)]))
    return next((name for name, bound in _FREQUENCIES if spacing <= bound), "annual")


def check_macro_gaps(
    observations: pd.DataFrame,
    as_of: AsOf,
    *,
    since: date,
    max_gap: FrequencyDays,
    max_age: FrequencyDays,
    severity: Severity,
) -> list[QualityIssue]:
    """Gaps in each macro series known at `as_of` (`macro_observations` rows; the latest known
    vintage per observation, with a value). The frequency is inferred from the observation
    dates (`infer_frequency`; series with fewer than three are skipped). A gap between two
    consecutive observations longer than `max_gap` (calendar days, for that frequency) is
    reported on the later observation's date if it is on or after `since`; a last observation
    older than `max_age` on `as_of.day` is reported on `as_of.day`. `symbol` is the series id.
    """
    latest = known_observations(observations, as_of)
    latest = latest[latest["value"].notna()]
    issues = []
    for series_id, rows in latest.groupby("series_id", sort=True):
        rows = rows.sort_values("obs_date", kind="stable")
        dates = list(rows["obs_date"])
        frequency = infer_frequency(dates)
        if frequency is None:
            continue
        stamps = list(rows[AVAILABLE_AT])
        gap_limit, age_limit = getattr(max_gap, frequency), getattr(max_age, frequency)
        for i in range(1, len(dates)):
            gap = (dates[i] - dates[i - 1]).days
            if gap > gap_limit and dates[i] >= since:
                text = (
                    f"no observation between {dates[i - 1].isoformat()} and "
                    f"{dates[i].isoformat()} ({gap} days; a {frequency} series allows {gap_limit})"
                )
                issues.append(
                    _issue(_MACRO, series_id, dates[i], "macro_gap", severity, text, stamps[i])
                )
        age = (as_of.day - dates[-1]).days
        if age > age_limit:
            text = (
                f"last observation {dates[-1].isoformat()} is {age} days old "
                f"(a {frequency} series allows {age_limit})"
            )
            issues.append(
                _issue(_MACRO, series_id, as_of.day, "macro_gap", severity, text, max(stamps))
            )
    return issues


# --- the report --------------------------------------------------------------------------------

_log = get_logger(__name__)


def latest_known_session(as_of: AsOf, calendar: TradingCalendar) -> date:
    """The latest session on or before `as_of.day` whose daily bar is known at `as_of` (at the
    default 08:00 cutoff, the session before `as_of.day`)."""
    latest = as_of.day if calendar.is_session(as_of.day) else calendar.prev_session(as_of.day)
    while not as_of.knows(bar_available_at(latest, calendar)):
        latest = calendar.prev_session(latest)
    return latest


def merge_issues(issues: pd.DataFrame) -> pd.DataFrame:
    """`issues` (`ISSUE_COLUMNS`) with one row per `(table, symbol, check, date)`: details
    joined with "; " in order, the highest severity and the latest `available_at`. Sorted by
    table, symbol, date and check."""
    if issues.empty:
        return issues[list(ISSUE_COLUMNS)].reset_index(drop=True)
    keys = ["table", "symbol", "check", "date"]
    grouped = issues.groupby(keys, sort=False)
    merged = grouped.agg(
        severity=("severity", lambda v: max(v, key=lambda x: _RANK[x])),
        detail=("detail", "; ".join),
        available_at=(AVAILABLE_AT, "max"),
    ).reset_index()
    merged = merged.sort_values(["table", "symbol", "date", "check"], kind="stable")
    return merged[list(ISSUE_COLUMNS)].reset_index(drop=True)


def blocked_symbols(issues: pd.DataFrame) -> frozenset[str]:
    """The symbols (or macro series ids) with at least one `critical` issue in `issues`."""
    return frozenset(str(s) for s in issues.loc[issues["severity"] == "critical", "symbol"])


def _known(
    lake: Lake, table: str, as_of: AsOf, filters: list[tuple[str, str, object]]
) -> pd.DataFrame:
    frame = lake.read_table(table, filters=filters)
    known = pit_filter(frame, as_of)
    assert_no_lookahead(known, as_of)
    return known


def quality_report(
    lake: Lake,
    as_of: date | AsOf,
    *,
    config: AtlasConfig | None = None,
    calendar: TradingCalendar | None = None,
    concept_map: ConceptMap | None = None,
    persist: bool = False,
    blocking_only: bool = False,
) -> pd.DataFrame:
    """Every quality check on the data known at `as_of`, as a frame of `ISSUE_COLUMNS`.

    A `date` means that session's decision cutoff (`config.calendar.decision_time`). The
    window is the last `data.quality.window_sessions` sessions whose bars are known at the
    cutoff (`latest_known_session`); earlier bars are read only as history for the outlier
    and stale-price checks. Fundamentals are checked for symbols with a filing that became
    public in the window; macro series on their latest known vintages. Only rows with
    `available_at` strictly before the cutoff are used. Issues sharing a key are merged
    (`merge_issues`). With `persist=True` the report replaces that day's rows of
    `data_quality_issues` (an empty report clears them). `config` defaults to `AtlasConfig()`,
    `calendar` to `TradingCalendar(config=config.calendar)`, `concept_map` to
    `config.data.fundamentals.xbrl_map_file` (loaded only when fundamentals are checked).

    `blocking_only=True` gives exactly the `critical` rows of the full report (the rows
    `blocked_symbols` reads) and runs only the checks that can produce one: a check whose
    configured severity is below `critical` is skipped and a table none of the remaining checks
    needs is not read. It cannot be combined with `persist` (`ValueError`): the stored table
    holds full reports.
    """
    if blocking_only and persist:
        raise ValueError("a blocking_only report cannot be persisted: it is not the full report")
    cfg = config if config is not None else AtlasConfig()
    cal = calendar if calendar is not None else TradingCalendar(config=cfg.calendar)
    if isinstance(as_of, datetime):
        raise TypeError("pass a trading day (date) or an AsOf, not a datetime")
    point = as_of if isinstance(as_of, AsOf) else AsOf.for_session(as_of, config=cfg.calendar)
    q = cfg.data.quality
    sev = q.severity
    last = latest_known_session(point, cal)
    since = cal.offset(last, -(q.window_sessions - 1))
    history = cal.offset(
        last, -(q.window_sessions + q.outlier_vol_window + q.stale_min_sessions + 1)
    )
    issues: list[QualityIssue] = []

    def runs(*checks: str) -> bool:
        """Whether a check emitting these issue names is run: always for a full report, for a
        blocking report only if one of them is configured `critical`."""
        return not blocking_only or any(
            Severity(getattr(sev, name)) is Severity.CRITICAL for name in checks
        )

    outliers = runs("return_outlier", "split_mismatch")
    price_checks = ("missing_sessions", "ohlc_inconsistent", "nonpositive_price", "stale_price")
    if lake.has_table(PRICES_DAILY.name) and (runs(*price_checks, "vendor_mix") or outliers):
        bars = _known(
            lake, PRICES_DAILY.name, point, [("date", ">=", history), ("date", "<=", last)]
        )
        if runs("missing_sessions"):
            issues += check_missing_sessions(
                bars, cal, since=since, severity=Severity(sev.missing_sessions)
            )
        if runs("ohlc_inconsistent"):
            issues += check_ohlc(bars, since=since, severity=Severity(sev.ohlc_inconsistent))
        if runs("nonpositive_price"):
            issues += check_nonpositive_prices(
                bars, since=since, severity=Severity(sev.nonpositive_price)
            )
        if runs("stale_price"):
            issues += check_stale_prices(
                bars,
                since=since,
                min_sessions=q.stale_min_sessions,
                severity=Severity(sev.stale_price),
            )
        if runs("vendor_mix"):
            issues += check_vendor_mix(bars, since=since, severity=Severity(sev.vendor_mix))
        if outliers:
            actions = _known(
                lake,
                CORPORATE_ACTIONS.name,
                point,
                [("ex_date", ">=", history), ("ex_date", "<=", last)],
            )
            issues += check_return_outliers(bars, actions, since=since, config=q)

    jumps = runs("fundamentals_jump")
    after_filing = runs("fundamentals_period_after_filing")
    if lake.has_table(FUNDAMENTALS_FACTS.name) and (jumps or after_filing):
        start = datetime.combine(since, time(0), tzinfo=MARKET_TZ)
        recent = _known(lake, FUNDAMENTALS_FACTS.name, point, [(AVAILABLE_AT, ">=", start)])
        symbols = sorted(set(recent["symbol"]))
        if symbols and jumps:
            facts = _known(lake, FUNDAMENTALS_FACTS.name, point, [("symbol", "in", symbols)])
            cmap = (
                concept_map
                if concept_map is not None
                else load_concept_map(cfg.data.fundamentals.xbrl_map_file)
            )
            issues += check_fundamentals_jumps(
                facts,
                point,
                since=since,
                concept_map=cmap,
                config=cfg.data.fundamentals,
                jump_ratio=q.fundamentals_jump_ratio,
                severity=Severity(sev.fundamentals_jump),
            )
        if symbols and after_filing:
            issues += check_fundamentals_period_after_filing(
                recent,
                point,
                since=since,
                severity=Severity(sev.fundamentals_period_after_filing),
            )

    if lake.has_table(MACRO_OBSERVATIONS.name) and runs("macro_gap"):
        observations = _known(lake, MACRO_OBSERVATIONS.name, point, [])
        issues += check_macro_gaps(
            observations,
            point,
            since=since,
            max_gap=q.macro_max_gap_days,
            max_age=q.macro_max_age_days,
            severity=Severity(sev.macro_gap),
        )

    report = merge_issues(issues_frame(issues))
    if blocking_only:
        report = report[report["severity"] == "critical"].reset_index(drop=True)
    assert_no_lookahead(report, point)
    if persist:
        _persist(lake, point.day, report)
    _log.info(
        "quality.report",
        day=point.day.isoformat(),
        window_start=since.isoformat(),
        last_session=last.isoformat(),
        issues=len(report),
        critical=int((report["severity"] == "critical").sum()),
        blocked=len(blocked_symbols(report)),
        persisted=persist,
        blocking_only=blocking_only,
    )
    return report


def _persist(lake: Lake, day: date, report: pd.DataFrame) -> None:
    """Replace the `data_quality_issues` rows of `day` with `report`: the day's stored rows are
    deleted and the new ones upserted, so only the files of `day`'s year are rewritten."""
    frame = report.assign(report_day=day)[list(DATA_QUALITY_ISSUES.column_names)]
    if not lake.has_table(DATA_QUALITY_ISSUES.name):
        lake.write_table(DATA_QUALITY_ISSUES.name, frame, mode="overwrite")  # creates the table
        return
    lake.delete_rows(DATA_QUALITY_ISSUES.name, [("report_day", "=", day)])
    if not frame.empty:
        lake.write_table(DATA_QUALITY_ISSUES.name, frame, mode="upsert")


def read_quality_issues(
    lake: Lake, as_of: date | AsOf, *, config: AtlasConfig | None = None
) -> pd.DataFrame:
    """The persisted issues of the report for `as_of`'s day (`ISSUE_COLUMNS`; empty when none
    was persisted). Every issue must have been known at `as_of` (`LookAheadError` otherwise:
    the stored report used data from after the cutoff)."""
    cfg = config if config is not None else AtlasConfig()
    if isinstance(as_of, datetime):
        raise TypeError("pass a trading day (date) or an AsOf, not a datetime")
    point = as_of if isinstance(as_of, AsOf) else AsOf.for_session(as_of, config=cfg.calendar)
    stored = lake.read_table(DATA_QUALITY_ISSUES.name, filters=[("report_day", "=", point.day)])
    assert_no_lookahead(stored, point)
    return stored[list(ISSUE_COLUMNS)].reset_index(drop=True)
