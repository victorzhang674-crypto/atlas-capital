"""The signal tear sheet (Stage 17): every research measure of one signal in one place.

`build_tearsheet(name, signal, labels)` (pure: no data reads) computes, for every horizon of
the labels, the Pearson and Spearman IC summaries, the IC by sector and by regime (Spearman),
the quantile analysis, the decay (mean IC per horizon, rank autocorrelation, turnover proxy)
and the vectorised long-short backtest; the backtest holds the last book for one evaluation
period (the median date spacing), to `backtest_end`, not to the end of the longest label.
The headline horizon (for the IC chart) is the largest horizon not longer than the spacing of
the evaluation dates (non-overlapping windows), else the shortest.

`research_signal(name, view_factory, start, end)` reads the data (a point-in-time signal panel
and the forward-return labels) and builds the sheet. `SignalTearSheet.save(out_dir)` writes
`tearsheet.json` (strict JSON: NaN as null), `ic_series.csv`, `quantile_returns.csv`,
`backtest_returns.csv` and one PNG per figure in `FIGURES`.
"""

import copy
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from matplotlib.figure import Figure

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.pit import guard_not_label
from atlas.core.run_context import current_run_context
from atlas.quant.research.ic import (
    METHODS,
    Method,
    ic_by_group,
    ic_by_regime,
    ic_series,
    nw_lags,
    rank_autocorrelation,
    session_step,
    summarize_ic,
)
from atlas.quant.research.labels import ForwardReturns, ViewFactory, forward_returns
from atlas.quant.research.quantiles import QuantileAnalysis, quantile_analysis
from atlas.quant.research.signals import Frequency, evaluation_dates, signal_panel
from atlas.quant.research.vector_backtest import BacktestResult, vector_backtest

_log = get_logger(__name__)

FIGURES = ("ic", "quantiles", "cumulative_spread", "decay", "backtest")
"""Figures of a tear sheet, saved as `<name>.png`."""

HEADLINE_METHOD: Method = "spearman"
"""IC method of the by-sector / by-regime tables and the IC chart."""


def _clean(value: Any) -> Any:
    """`value` as strict-JSON-ready Python: NaN/inf -> None, numpy scalars -> Python."""
    if isinstance(value, Mapping):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _table(frame: pd.DataFrame) -> dict[str, Any]:
    return {str(k): row for k, row in frame.to_dict(orient="index").items()}


@dataclass(frozen=True, eq=False)
class SignalTearSheet:
    """The research results of signal `name` (module docstring): `summary` (strict-JSON-ready
    dict), `ic` (date x `h<h>_<method>` IC series), `quantiles` (horizon -> analysis),
    `autocorrelation` (lag -> mean rank autocorrelation) and `backtest`."""

    name: str
    summary: dict[str, Any]
    ic: pd.DataFrame
    quantiles: Mapping[int, QuantileAnalysis]
    autocorrelation: pd.Series
    backtest: BacktestResult
    headline_horizon: int
    rolling_window: int

    def to_dict(self) -> dict[str, Any]:
        """A deep copy of `summary`."""
        return copy.deepcopy(self.summary)

    def figures(self) -> dict[str, Figure]:
        """The figures of `FIGURES` (matplotlib `Figure`s, no global pyplot state)."""
        return {
            "ic": self._ic_figure(),
            "quantiles": self._quantile_figure(),
            "cumulative_spread": self._spread_figure(),
            "decay": self._decay_figure(),
            "backtest": self._backtest_figure(),
        }

    def save(self, out_dir: Path) -> tuple[Path, ...]:
        """Write the tear sheet into `out_dir` (created if needed); returns the files."""
        out_dir.mkdir(parents=True, exist_ok=True)
        written = [out_dir / "tearsheet.json"]
        written[0].write_text(json.dumps(self.summary, indent=2, allow_nan=False) + "\n")
        ic_path = out_dir / "ic_series.csv"
        self.ic.to_csv(ic_path, index_label="date")
        quant_path = out_dir / "quantile_returns.csv"
        frames = [
            qa.by_date.rename(columns=lambda b: f"q{b}").assign(horizon=h)
            for h, qa in self.quantiles.items()
        ]
        pd.concat(frames).to_csv(quant_path, index_label="date")
        bt_path = out_dir / "backtest_returns.csv"
        bt = self.backtest
        pd.DataFrame(
            {
                "gross_return": bt.gross_returns,
                "cost": bt.costs,
                "net_return": bt.returns,
                "cumulative": (1.0 + bt.returns).cumprod() - 1.0,
            }
        ).to_csv(bt_path, index_label="date")
        written += [ic_path, quant_path, bt_path]
        for name, figure in self.figures().items():
            path = out_dir / f"{name}.png"
            figure.savefig(path, dpi=110, bbox_inches="tight")
            written.append(path)
        return tuple(written)

    # --- figures ---------------------------------------------------------------------------------

    def _ic_figure(self) -> Figure:
        h = self.headline_horizon
        series = self.ic[f"h{h}_{HEADLINE_METHOD}"]
        fig = Figure(figsize=(10, 4))
        ax = fig.add_subplot()
        ax.bar(series.index, series.to_numpy(), width=3, color="#7a9cc6", label="IC per date")
        rolling = series.rolling(self.rolling_window, min_periods=1).mean()
        ax.plot(rolling.index, rolling.to_numpy(), color="#1f3b73", label="rolling mean")
        ax.axhline(0, color="black", linewidth=0.6)
        ax.set_title(f"{self.name}: {HEADLINE_METHOD} IC, {h}-session forward return")
        ax.legend(loc="upper left")
        return fig

    def _quantile_figure(self) -> Figure:
        fig = Figure(figsize=(10, 4))
        ax = fig.add_subplot()
        horizons = sorted(self.quantiles)
        width = 0.8 / len(horizons)
        for i, h in enumerate(horizons):
            mean = self.quantiles[h].mean
            x = np.arange(len(mean)) + i * width
            ax.bar(x, mean.to_numpy() * 1e4, width=width, label=f"h = {h} sessions")
        first = self.quantiles[horizons[0]].mean
        ax.set_xticks(np.arange(len(first)) + 0.4 - width / 2, [f"Q{b}" for b in first.index])
        ax.axhline(0, color="black", linewidth=0.6)
        ax.set_ylabel("mean forward return (bps)")
        ax.set_title(f"{self.name}: mean forward return by quantile (Q1 = lowest signal)")
        ax.legend()
        return fig

    def _spread_figure(self) -> Figure:
        spread = self.quantiles[min(self.quantiles)].cumulative_spread
        fig = Figure(figsize=(10, 4))
        ax = fig.add_subplot()
        ax.plot(spread.index, spread.to_numpy() * 100, color="#1f3b73")
        ax.axhline(0, color="black", linewidth=0.6)
        ax.set_ylabel("cumulative top - bottom (%)")
        ax.set_title(f"{self.name}: cumulative daily quantile spread (equal weight, no costs)")
        return fig

    def _decay_figure(self) -> Figure:
        fig = Figure(figsize=(10, 4))
        left, right = fig.subplots(1, 2)
        decay = self.summary["decay"]["ic_by_horizon"]
        for method in METHODS:
            points = sorted((int(h), v) for h, v in decay[method].items() if v is not None)
            if points:
                left.plot(*zip(*points, strict=True), marker="o", label=method)
        left.axhline(0, color="black", linewidth=0.6)
        left.set_xlabel("horizon (sessions)")
        left.set_title("mean IC by horizon")
        left.legend()
        ac = self.autocorrelation
        positions = np.arange(len(ac))
        right.bar(positions, ac.to_numpy(), color="#7a9cc6")
        right.set_xticks(positions, [str(k) for k in ac.index])
        right.set_xlabel("lag (evaluation dates)")
        right.set_title("signal rank autocorrelation")
        fig.suptitle(f"{self.name}: decay")
        return fig

    def _backtest_figure(self) -> Figure:
        bt = self.backtest
        fig = Figure(figsize=(10, 4))
        ax = fig.add_subplot()
        for series, label in ((bt.gross_returns, "gross"), (bt.returns, "net of costs")):
            wealth = (1.0 + series).cumprod() - 1.0
            ax.plot(wealth.index, wealth.to_numpy() * 100, label=label)
        ax.axhline(0, color="black", linewidth=0.6)
        ax.set_ylabel("cumulative return (%)")
        s = bt.summary
        ax.set_title(
            f"{self.name}: top-bottom quantile L/S ({s['weighting']}, {s['rebalance']}, "
            f"{s['cost_bps']} bps)"
        )
        ax.legend()
        return fig


def headline_horizon(horizons: tuple[int, ...], step: int) -> int:
    """The largest of `horizons` not longer than `step` sessions, else the shortest."""
    fitting = [h for h in horizons if h <= step]
    return max(fitting) if fitting else min(horizons)


def build_tearsheet(
    name: str,
    signal: pd.DataFrame,
    labels: ForwardReturns,
    *,
    sectors: pd.Series | None = None,
    regimes: pd.Series | None = None,
    kind: str | None = None,
    frequency: str | None = None,
    universe: str | None = None,
    config: AtlasConfig | None = None,
) -> SignalTearSheet:
    """The tear sheet of `signal` (evaluation dates x symbols) against `labels` (module
    docstring). `sectors`: symbol -> sector (`None` unknown, never pooled); `regimes`: date ->
    regime label (Stage 21; without it the by-regime section says it is not available).
    `kind`, `frequency` and `universe` are recorded in the summary, and so is the active run
    (`run_id`, `mode`, `config_fingerprint`, `stage`; `None` outside a `run_context`). The
    turnover proxy is 1 - the lag-1 rank autocorrelation, whatever lags are configured."""
    guard_not_label(signal, "signal")
    cfg = config if config is not None else AtlasConfig()
    rc = cfg.quant.research
    calendar = TradingCalendar(config=cfg.calendar)
    dates = list(signal.index)
    step = session_step(dates, calendar)
    headline = headline_horizon(labels.horizons, step)
    ic_cols: dict[str, pd.Series] = {}
    ic_summary: dict[str, Any] = {}
    by_sector: dict[str, Any] = {}
    by_regime: dict[str, Any] = {"available": regimes is not None}
    quantiles: dict[int, QuantileAnalysis] = {}
    for h in labels.horizons:
        target = labels.horizon(h)
        lags = nw_lags(h, dates, calendar)
        ic_summary[f"h{h}"] = {}
        for method in METHODS:
            series = ic_series(signal, target, method=method, config=cfg)
            ic_cols[f"h{h}_{method}"] = series
            ic_summary[f"h{h}"][method] = summarize_ic(series, lags).to_dict()
        if sectors is not None:
            table = ic_by_group(
                signal, target, sectors, lags=lags, method=HEADLINE_METHOD, config=cfg
            )
            by_sector[f"h{h}"] = {"method": HEADLINE_METHOD, "groups": _table(table)}
        if regimes is not None:
            table = ic_by_regime(ic_cols[f"h{h}_{HEADLINE_METHOD}"], regimes, lags=lags)
            by_regime[f"h{h}"] = _table(table)
        quantiles[h] = quantile_analysis(signal, labels, h, lags=lags, config=cfg)
    if sectors is not None:
        known = sectors.reindex(signal.columns)
        by_sector["names_without_sector"] = int(known.map(lambda v: not isinstance(v, str)).sum())
    else:
        by_sector = {"available": False, "note": "no sectors supplied"}
    if regimes is None:
        by_regime["note"] = "no regimes supplied (regime detection is Stage 21)"
    ac = rank_autocorrelation(signal, rc.autocorr_lags, config=cfg)
    lag1 = ac.loc[1] if 1 in ac.index else rank_autocorrelation(signal, (1,), config=cfg).loc[1]
    backtest_end = calendar.offset(pd.Timestamp(dates[-1]).date(), step)
    backtest = vector_backtest(signal, labels.daily.loc[: pd.Timestamp(backtest_end)], config=cfg)
    decay = {
        "ic_by_horizon": {
            method: {str(h): ic_summary[f"h{h}"][method]["mean"] for h in labels.horizons}
            for method in METHODS
        },
        "rank_autocorrelation": {str(k): v for k, v in ac.items()},
        "turnover_proxy": 1.0 - float(lag1),
    }
    ctx = current_run_context()
    run = (
        None
        if ctx is None
        else {
            "run_id": ctx.run_id,
            "mode": ctx.mode,
            "config_fingerprint": ctx.config_fingerprint,
            "stage": ctx.stage,
        }
    )
    summary = {
        "signal": name,
        "run": run,
        "kind": kind,
        "frequency": frequency,
        "universe": universe,
        "start": str(pd.Timestamp(dates[0]).date()) if dates else None,
        "end": str(pd.Timestamp(dates[-1]).date()) if dates else None,
        "n_dates": len(dates),
        "n_symbols": int(signal.notna().any().sum()),
        "date_step_sessions": step,
        "headline_horizon": headline,
        "labels": {
            "horizons": list(labels.horizons),
            "known_at": labels.known_at.cutoff.isoformat(),
            "blocked": list(labels.blocked),
        },
        "ic": ic_summary,
        "decay": decay,
        "quantiles": {
            f"h{h}": {
                "mean_by_bucket": {str(b): v for b, v in qa.mean.items()},
                "spread_mean": qa.spread_mean,
                "spread_t": qa.spread_t,
                "lags": qa.lags,
                "monotonicity": qa.monotonicity,
                "mean_names_per_bucket": {str(b): float(v) for b, v in qa.counts.mean().items()},
            }
            for h, qa in quantiles.items()
        },
        "cumulative_spread_final": float(quantiles[labels.horizons[0]].cumulative_spread.iloc[-1])
        if len(quantiles[labels.horizons[0]].cumulative_spread)
        else None,
        "by_sector": by_sector,
        "by_regime": by_regime,
        "backtest": backtest.summary,
        "backtest_end": str(backtest_end),
        "settings": rc.model_dump(mode="json"),
    }
    return SignalTearSheet(
        name=name,
        summary=_clean(summary),
        ic=pd.DataFrame(ic_cols, index=signal.index),
        quantiles=quantiles,
        autocorrelation=ac,
        backtest=backtest,
        headline_horizon=headline,
        rolling_window=rc.rolling_ic_window,
    )


def research_signal(
    name: str,
    view_factory: ViewFactory,
    start: date,
    end: date,
    *,
    config: AtlasConfig | None = None,
    frequency: Frequency | None = None,
    universe: str | None = None,
    regimes: pd.Series | None = None,
    progress: Callable[[date], None] | None = None,
) -> SignalTearSheet:
    """Evaluate signal `name` on the evaluation dates in [`start`, `end`] (`frequency`,
    default `quant.research.frequency`): the point-in-time signal panel (`signal_panel`, the
    members of `universe` at each date), the labels (`forward_returns`) and the tear sheet.
    `DataError` when no name has a signal on any date."""
    cfg = config if config is not None else AtlasConfig()
    rc = cfg.quant.research
    freq: Frequency = rc.frequency if frequency is None else frequency
    univ = rc.universe if universe is None else universe
    calendar = TradingCalendar(config=cfg.calendar)
    dates = evaluation_dates(start, end, freq, calendar)
    if not dates:
        raise ValueError(f"no trading sessions between {start} and {end}")
    panel = signal_panel(
        name, view_factory, dates, universe=univ, calendar=calendar, progress=progress
    )
    if panel.values.empty or not panel.values.notna().any().any():
        raise DataError(f"no member of {univ!r} has a value of {name!r} in {start}..{end}")
    labels = forward_returns(view_factory, dates, list(panel.values.columns), config=cfg)
    _log.info(
        "research.signal_evaluated",
        signal=name,
        dates=len(dates),
        symbols=len(panel.values.columns),
        blocked=len(labels.blocked),
    )
    return build_tearsheet(
        name,
        panel.values,
        labels,
        sectors=panel.sectors,
        regimes=regimes,
        kind=panel.kind,
        frequency=freq,
        universe=univ,
        config=cfg,
    )
