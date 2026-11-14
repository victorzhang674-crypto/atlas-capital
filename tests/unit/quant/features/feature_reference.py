"""Independent reference implementation of the Stage 15 technical features, for tests only.

Plain Python: one loop per symbol and date, `math` on floats, no pandas rolling or ewm, so it
shares no code with `atlas.quant.features`. It follows the definitions in the Stage 15 plan
(D6). Run as a script to (re)write the fixtures:

    uv run python tests/unit/quant/features/feature_reference.py

`tests/fixtures/features/ohlcv.csv` holds the inputs (330 sessions of AAA with a 2:1 split and
a cash dividend, BBB with one missing bar, SPY), `expected.csv` the reference values of every
feature for AAA and BBB over the last `EXPECTED_SESSIONS` sessions (12 significant digits).
"""

import csv
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

NAN = math.nan
FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "features"
OHLCV_CSV = FIXTURES / "ohlcv.csv"
EXPECTED_CSV = FIXTURES / "expected.csv"
N_SESSIONS = 330
EXPECTED_SESSIONS = 60
SYMBOLS = ("AAA", "BBB", "SPY")
TARGETS = ("AAA", "BBB")
BENCHMARK = "SPY"
PER_YEAR = 252
SPLIT_AT = 150  # AAA: 2:1 split, ex-date = session index 150
DIVIDEND_AT = 220  # AAA: cash dividend of 1.50 per (post-split) share, ex-date index 220
DIVIDEND = 1.5
MISSING_AT = 300  # BBB: no bar on this session (inside the expected window)
FLAT_EPS = 1e-12  # a band width / standard deviation at or below this counts as zero
OHLCV_COLUMNS = ("date", "symbol", "open", "high", "low", "close", "volume", "adj_close")


def ok(x: float) -> bool:
    """Whether `x` is a number (not NaN)."""
    return not math.isnan(x)


# --- the input series --------------------------------------------------------------------------


def sessions() -> list[date]:
    """The `N_SESSIONS` XNYS sessions ending 2024-02-29."""
    from atlas.core.calendar import TradingCalendar

    cal = TradingCalendar()
    last = date(2024, 2, 29)
    return list(cal.sessions(cal.offset(last, -(N_SESSIONS - 1)), last))


def make_ohlcv(seed: int = 1515) -> list[dict[str, object]]:
    """Raw daily bars (vendor style) of AAA, BBB and SPY with our `adj_close` (CRSP-style
    backward adjustment relative to the last bar)."""
    rng = np.random.default_rng(seed)
    days = sessions()
    n = len(days)
    m = rng.normal(0.0004, 0.01, n)
    rets = {
        "SPY": m,
        "AAA": 0.0003 + 1.2 * m + rng.normal(0.0, 0.015, n),
        "BBB": -0.0001 + 0.8 * m + rng.normal(0.0, 0.012, n),
    }
    rows: list[dict[str, object]] = []
    for symbol in SYMBOLS:
        r = rets[symbol]
        close = [0.0] * n
        close[0] = {"SPY": 400.0, "AAA": 180.0, "BBB": 60.0}[symbol]
        for t in range(1, n):
            price = close[t - 1] * (1.0 + r[t])
            if symbol == "AAA" and t == SPLIT_AT:
                price /= 2.0
            if symbol == "AAA" and t == DIVIDEND_AT:
                price -= DIVIDEND
            close[t] = price
        factor = [1.0] * n
        if symbol == "AAA":
            for t in range(n):
                if t < SPLIT_AT:
                    factor[t] *= 0.5
                if t < DIVIDEND_AT:
                    factor[t] *= 1.0 - DIVIDEND / close[DIVIDEND_AT - 1]
        gaps = rng.normal(0.0, 0.004, n)
        up = np.abs(rng.normal(0.0, 0.006, n))
        down = np.abs(rng.normal(0.0, 0.006, n))
        vols = np.exp(rng.normal(0.0, 0.35, n))
        for t in range(n):
            if symbol == "BBB" and t == MISSING_AT:
                continue
            prev = close[t - 1] if t > 0 else close[0]
            if symbol == "AAA" and t == SPLIT_AT:
                prev /= 2.0
            open_ = prev * (1.0 + gaps[t])
            high = max(open_, close[t]) * (1.0 + up[t])
            low = min(open_, close[t]) * (1.0 - down[t])
            shares = 3_000_000 * vols[t] * (2.0 if symbol == "AAA" and t >= SPLIT_AT else 1.0)
            rows.append(
                {
                    "date": days[t],
                    "symbol": symbol,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close[t],
                    "volume": round(shares),
                    "adj_close": close[t] * factor[t],
                }
            )
    return rows


@dataclass(frozen=True)
class Series:
    """One symbol on the session grid (NaN where there is no bar): adjusted open/high/low/
    close (USD), raw dollar volume (USD), share volume in today's shares (`None`: unknown)."""

    o: list[float]
    h: list[float]
    lo: list[float]
    c: list[float]
    dv: list[float]
    sv: list[float] | None = None


def split_factor(symbol: str, t: int) -> float:
    """The fixture's split factor of session `t`: AAA's bars before its 2:1 split are in
    pre-split shares (factor 1/2), every other bar in today's (1)."""
    return 0.5 if symbol == "AAA" and t < SPLIT_AT else 1.0


def to_series(rows: Sequence[dict[str, object]], days: Sequence[date]) -> dict[str, Series]:
    """Per-symbol `Series` of `rows` on `days`."""
    pos = {d: i for i, d in enumerate(days)}
    out: dict[str, Series] = {}
    for symbol in SYMBOLS:
        keys = ("o", "h", "lo", "c", "dv", "sv")
        cols: dict[str, list[float]] = {k: [NAN] * len(days) for k in keys}
        for row in rows:
            if row["symbol"] != symbol:
                continue
            t = pos[row["date"]]  # type: ignore[index]
            close, adj = float(row["close"]), float(row["adj_close"])  # type: ignore[arg-type]
            f = adj / close
            cols["o"][t] = float(row["open"]) * f  # type: ignore[arg-type]
            cols["h"][t] = float(row["high"]) * f  # type: ignore[arg-type]
            cols["lo"][t] = float(row["low"]) * f  # type: ignore[arg-type]
            cols["c"][t] = adj
            cols["dv"][t] = close * float(row["volume"])  # type: ignore[arg-type]
            cols["sv"][t] = float(row["volume"]) / split_factor(symbol, t)  # type: ignore[arg-type]
        out[symbol] = Series(**cols)
    return out


# --- building blocks ---------------------------------------------------------------------------


def window(x: Sequence[float], t: int, n: int) -> list[float] | None:
    """`x[t-n+1 .. t]` if all present and in range, else `None`."""
    if t - n + 1 < 0:
        return None
    w = list(x[t - n + 1 : t + 1])
    return w if all(ok(v) for v in w) else None


def mean(w: Sequence[float]) -> float:
    return sum(w) / len(w)


def std(w: Sequence[float], ddof: int) -> float:
    mu = mean(w)
    return math.sqrt(sum((v - mu) ** 2 for v in w) / (len(w) - ddof))


def rolling(x: Sequence[float], n: int, f: Callable[[list[float]], float]) -> list[float]:
    out = []
    for t in range(len(x)):
        w = window(x, t, n)
        out.append(f(w) if w is not None else NAN)
    return out


def smooth(x: Sequence[float], n: int, alpha: float) -> list[float]:
    """Recursive smoother: seeded with the mean of the first `n` consecutive present values,
    then `s += alpha * (x - s)`; a missing input keeps the state and gives NaN."""
    out = [NAN] * len(x)
    state: float | None = None
    for t in range(len(x)):
        if state is None:
            w = window(x, t, n)
            if w is not None:
                state = mean(w)
                out[t] = state
        elif ok(x[t]):
            state = state + alpha * (x[t] - state)
            out[t] = state
    return out


def ema(x: Sequence[float], n: int) -> list[float]:
    return smooth(x, n, 2.0 / (n + 1))


def wilder(x: Sequence[float], n: int) -> list[float]:
    return smooth(x, n, 1.0 / n)


def returns(c: Sequence[float]) -> list[float]:
    return [NAN] + [
        c[t] / c[t - 1] - 1 if ok(c[t]) and ok(c[t - 1]) else NAN for t in range(1, len(c))
    ]


def lagged_ratio(c: Sequence[float], a: int, b: int) -> list[float]:
    """`c[t-a] / c[t-b] - 1` (a < b)."""
    out = []
    for t in range(len(c)):
        if t - b < 0 or not (ok(c[t - a]) and ok(c[t - b])):
            out.append(NAN)
        else:
            out.append(c[t - a] / c[t - b] - 1)
    return out


def binary(
    x: Sequence[float], y: Sequence[float], f: Callable[[float, float], float]
) -> list[float]:
    return [f(a, b) if ok(a) and ok(b) else NAN for a, b in zip(x, y, strict=True)]


# --- the features -------------------------------------------------------------------------------


def features(s: Series, bench: Series) -> dict[str, list[float]]:
    """Every Stage 15 feature of one symbol."""
    c, o, h, lo, dv = s.c, s.o, s.h, s.lo, s.dv
    r = returns(c)
    m = returns(bench.c)
    root = math.sqrt(PER_YEAR)
    out: dict[str, list[float]] = {}
    for n in (1, 5, 21, 63, 126, 252):
        out[f"ret_{n}d"] = lagged_ratio(c, 0, n)
    out["momentum_12_1"] = lagged_ratio(c, 21, 252)
    out["reversal_5d"] = [-v for v in out["ret_5d"]]
    out["vol_21d"] = rolling(r, 21, lambda w: std(w, 1) * root)
    out["vol_63d"] = rolling(r, 63, lambda w: std(w, 1) * root)
    out["downside_vol_63d"] = rolling(
        r, 63, lambda w: math.sqrt(mean([min(v, 0.0) ** 2 for v in w])) * root
    )
    tr = [NAN] * len(c)
    for t in range(1, len(c)):
        if ok(h[t]) and ok(lo[t]) and ok(c[t - 1]):
            tr[t] = max(h[t] - lo[t], abs(h[t] - c[t - 1]), abs(lo[t] - c[t - 1]))
    out["atr_14"] = wilder(tr, 14)
    out["atr_pct_14"] = binary(out["atr_14"], c, lambda a, b: a / b)
    delta = binary(c[1:], c[:-1], lambda a, b: a - b)
    delta = [NAN, *delta]
    gains = [max(d, 0.0) if ok(d) else NAN for d in delta]
    losses = [max(-d, 0.0) if ok(d) else NAN for d in delta]
    avg_gain, avg_loss = wilder(gains, 14), wilder(losses, 14)

    def rsi(g: float, loss: float) -> float:
        if loss == 0.0:
            return 100.0 if g > 0.0 else 50.0
        return 100.0 - 100.0 / (1.0 + g / loss)

    out["rsi_14"] = binary(avg_gain, avg_loss, rsi)
    macd = binary(ema(c, 12), ema(c, 26), lambda a, b: a - b)
    signal = ema(macd, 9)
    out["macd"] = macd
    out["macd_signal"] = signal
    out["macd_hist"] = binary(macd, signal, lambda a, b: a - b)

    def pctb(t: int) -> float:
        w = window(c, t, 20)
        if w is None:
            return NAN
        mid, sd = mean(w), std(w, 0)
        if 4.0 * sd <= FLAT_EPS * abs(mid):
            return NAN
        lower = mid - 2.0 * sd
        return (c[t] - lower) / (4.0 * sd)

    out["bb_pctb_20"] = [pctb(t) for t in range(len(c))]
    for n in (20, 50, 200):
        sma, e = rolling(c, n, mean), ema(c, n)
        out[f"sma_{n}"] = sma
        out[f"ema_{n}"] = e
    for n in (20, 50, 200):
        out[f"price_to_sma_{n}"] = binary(c, out[f"sma_{n}"], lambda a, b: a / b)
        out[f"price_to_ema_{n}"] = binary(c, out[f"ema_{n}"], lambda a, b: a / b)
    out["dist_52w_high"] = binary(c, rolling(h, 252, max), lambda a, b: a / b - 1)
    out["dist_52w_low"] = binary(c, rolling(lo, 252, min), lambda a, b: a / b - 1)

    def max_dd(w: list[float]) -> float:
        peak, worst = w[0], 0.0
        for v in w:
            peak = max(peak, v)
            worst = min(worst, v / peak - 1)
        return worst

    out["max_dd_252d"] = rolling(c, 252, max_dd)
    beta, idio = [NAN] * len(c), [NAN] * len(c)
    for t in range(len(c)):
        wr, wm = window(r, t, 252), window(m, t, 252)
        if wr is None or wm is None:
            continue
        mr, mm = mean(wr), mean(wm)
        sxx = sum((x - mm) ** 2 for x in wm)
        if sxx <= FLAT_EPS:
            continue
        b = sum((x - mm) * (y - mr) for x, y in zip(wm, wr, strict=True)) / sxx
        a = mr - b * mm
        ssr = sum((y - a - b * x) ** 2 for x, y in zip(wm, wr, strict=True))
        beta[t] = b
        idio[t] = math.sqrt(ssr / (len(wr) - 2)) * root
    out["beta_252d"] = beta
    out["idio_vol_252d"] = idio
    logdv = [math.log(v) if ok(v) and v > 0 else NAN for v in dv]
    vz = [NAN] * len(c)
    for t in range(len(c)):
        w = window(logdv, t - 1, 21) if t >= 1 else None
        if w is None or not ok(logdv[t]):
            continue
        sd = std(w, 1)
        vz[t] = (logdv[t] - mean(w)) / sd if sd > FLAT_EPS else NAN
    out["volume_z_21d"] = vz
    sv = s.sv if s.sv is not None else [NAN] * len(c)
    logsv = [math.log(v) if ok(v) and v > 0 else NAN for v in sv]
    svz = [NAN] * len(c)
    for t in range(len(c)):
        w = window(logsv, t - 1, 21) if t >= 1 else None
        if w is None or not ok(logsv[t]):
            continue
        sd = std(w, 1)
        svz[t] = (logsv[t] - mean(w)) / sd if sd > FLAT_EPS else NAN
    out["share_volume_z_21d"] = svz
    illiq = binary(r, dv, lambda a, b: abs(a) / b * 1e6 if b > 0 else NAN)
    out["amihud_21d"] = rolling(illiq, 21, mean)
    gap = [NAN] + [
        o[t] / c[t - 1] - 1 if ok(o[t]) and ok(c[t - 1]) else NAN for t in range(1, len(c))
    ]
    out["gap_1d"] = gap
    out["gap_mean_21d"] = rolling(gap, 21, mean)
    out["gap_std_63d"] = rolling(gap, 63, lambda w: std(w, 1))
    return out


FEATURE_NAMES = tuple(features(Series(*([[1.0] * 3] * 5)), Series(*([[1.0] * 3] * 5))))
"""The 41 feature names, in the reference's order."""


# --- fixture files ------------------------------------------------------------------------------


def write_fixtures() -> None:
    """Write `ohlcv.csv` and `expected.csv`."""
    FIXTURES.mkdir(parents=True, exist_ok=True)
    rows = make_ohlcv()
    with OHLCV_CSV.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(OHLCV_COLUMNS), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {k: (f"{v:.12g}" if isinstance(v, float) else v) for k, v in row.items()}
            )
    with EXPECTED_CSV.open("w", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["date", "symbol", *FEATURE_NAMES])
        for day, symbol, values in expected_rows(read_ohlcv()):
            writer.writerow([day.isoformat(), symbol, *(_fmt(v) for v in values)])


def _fmt(v: float) -> str:
    return "" if math.isnan(v) else f"{v:.12g}"


def read_ohlcv() -> list[dict[str, object]]:
    """The rows of `ohlcv.csv` (dates parsed, numbers as floats / int volume)."""
    out: list[dict[str, object]] = []
    with OHLCV_CSV.open(newline="") as fh:
        for row in csv.DictReader(fh):
            out.append(
                {
                    "date": date.fromisoformat(row["date"]),
                    "symbol": row["symbol"],
                    **{k: float(row[k]) for k in ("open", "high", "low", "close", "adj_close")},
                    "volume": int(row["volume"]),
                }
            )
    return out


def expected_rows(rows: Sequence[dict[str, object]]) -> list[tuple[date, str, list[float]]]:
    """Reference values of every feature for `TARGETS` over the last `EXPECTED_SESSIONS`
    sessions of `rows`: `(date, symbol, values in FEATURE_NAMES order)`."""
    days = sorted({r["date"] for r in rows})  # type: ignore[type-var]
    series = to_series(rows, days)  # type: ignore[arg-type]
    computed = {s: features(series[s], series[BENCHMARK]) for s in TARGETS}
    out = []
    for t in range(len(days) - EXPECTED_SESSIONS, len(days)):
        for s in TARGETS:
            out.append((days[t], s, [computed[s][name][t] for name in FEATURE_NAMES]))  # type: ignore[misc]
    return out


def split_frame(days: Sequence[date], symbols: Sequence[str] = TARGETS) -> pd.DataFrame:
    """`split_factor` as a `DataView.split_factors`-shaped frame on `days` (for the tests of
    the production code; the reference itself uses `split_factor`)."""
    index = pd.DatetimeIndex(pd.to_datetime(list(days)), name="date")
    data = {s: [split_factor(s, t) for t in range(len(days))] for s in symbols}
    return pd.DataFrame(data, index=index)


def read_expected() -> list[tuple[date, str, list[float]]]:
    """The rows of `expected.csv` (empty cell = NaN)."""
    with EXPECTED_CSV.open(newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        assert tuple(header[2:]) == FEATURE_NAMES
        return [
            (date.fromisoformat(r[0]), r[1], [float(v) if v else NAN for v in r[2:]])
            for r in reader
        ]


if __name__ == "__main__":
    write_fixtures()
