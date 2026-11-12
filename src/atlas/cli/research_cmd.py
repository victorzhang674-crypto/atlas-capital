"""`atlas research …`: signal research (Stage 17).

`atlas research signal --name momentum_12_1 --from 2016 --to 2021` evaluates a Stage 15
technical feature or a Stage 16 fundamental factor on the evaluation dates in the window
(point in time: one `DataView` per date, the universe's members at that date), against
forward returns, and writes its tear sheet (`tearsheet.json`, CSVs, PNG figures) to
`<lake>/<quant.research.out_dir>/<name>/` or `--out`. It only reads the lake. It runs inside a
`run_context` (stage `research.signal`), so its logs carry the `run_id` and the tear sheet
records the run and the config fingerprint; the log goes to `research.log` (JSON lines) in the
output directory, the console shows warnings and errors only. A bare year
means 1 January (`--from`) or 31 December (`--to`). Exit codes: 0 ok, 1 the run failed,
2 bad arguments.

Hold-out guard (spec A7): the labels of the last date reach `max(quant.research.horizons)`
sessions past it, and a window whose labels close after `backtest.in_sample_end` is refused
(exit 2, with the last `--to` that fits: 2021-10-01 for the default 63-session horizon)
unless `--allow-holdout` is given. The override is written to the run's audit log
(`<lake>/runs/<run_id>/audit.jsonl`, an `OVERRIDE` event) before any data is read; if it
cannot be written, the run stops (exit 1). `--regimes vol|trend|quadrant` adds the IC by
regime from the stored `regime_history` (`atlas regime build`).

`atlas research pairs --as-of 2021-12-01` (Stage 23) selects the cointegrated same-sector
pairs at the as-of (point in time: the universe's members on that day, closes through the
session before) and lists them with their current spread z-score and the signal a flat pair
would take now (a stateless snapshot), writing `pairs.csv` and `pairs.json` to
`<lake>/<quant.research.out_dir>/pairs/<as-of>/` or `--out`. It only reads the lake.
"""

import json
import re
from datetime import date
from pathlib import Path
from typing import Annotated, Any, cast

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from atlas.core.audit import AuditLog, EventType, audit_path
from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig, load_config
from atlas.core.errors import AtlasError, ConfigError, DataError
from atlas.core.logging import configure_logging, reset_logging
from atlas.core.run_context import run_context
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.quant.regime import REGIME_KINDS, RegimeStore
from atlas.quant.research import (
    FREQUENCIES,
    Frequency,
    HoldoutCheck,
    SignalTearSheet,
    evaluation_dates,
    holdout_check,
    research_signal,
    signal_kind,
)
from atlas.quant.statarb import PairsSnapshot, pairs_snapshot

research_app = typer.Typer(
    help="Signal research: information coefficients, quantiles, decay, long-short backtest.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)

CONSOLE_MIN_WIDTH = 160
AUDIT_ACTOR = "cli"
_YEAR = re.compile(r"^\d{4}$")


def parse_bound(text: str, *, end: bool = False) -> date:
    """`YYYY` (1 January, or 31 December when `end`) or `YYYY-MM-DD` as a `date`;
    `typer.BadParameter` otherwise."""
    value = text.strip()
    try:
        if _YEAR.match(value):
            return date(int(value), 12, 31) if end else date(int(value), 1, 1)
        return date.fromisoformat(value)
    except ValueError:
        raise typer.BadParameter(f"{text!r} is not a year (YYYY) or a date (YYYY-MM-DD)") from None


def _console() -> Console:
    console = Console(soft_wrap=False, highlight=False)
    if not console.is_terminal and console.width < CONSOLE_MIN_WIDTH:
        console = Console(soft_wrap=False, highlight=False, width=CONSOLE_MIN_WIDTH)
    return console


def _config(ctx: typer.Context, console: Console) -> AtlasConfig:
    obj = ctx.find_root().obj
    profile = getattr(obj, "profile", "dev")
    config_dir = Path(getattr(obj, "config_dir", Path("config")))
    try:
        return load_config(profile, config_dir=config_dir)
    except ConfigError as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        raise typer.Exit(1) from None


def _fmt(value: Any, spec: str) -> str:
    return "-" if value is None else format(value, spec)


def _summary_table(sheet: SignalTearSheet) -> Table:
    s = sheet.summary
    with_ic = s["ic"][f"h{s['headline_horizon']}"]["spearman"]["n_dates"]
    counted = f"{with_ic} dates with an IC"
    if with_ic != s["n_dates"]:
        counted += f" ({s['n_dates']} requested)"
    table = Table(title=f"{s['signal']} ({s['kind']}): {s['start']} .. {s['end']}, {counted}")
    for name in (
        "horizon",
        "IC pearson",
        "IC spearman",
        "NW t",
        "IR",
        "hit rate",
        "Q5-Q1",
        "monotonic",
    ):
        table.add_column(name, justify="right")
    for h in s["labels"]["horizons"]:
        ic, qa = s["ic"][f"h{h}"], s["quantiles"][f"h{h}"]
        sp = ic["spearman"]
        table.add_row(
            str(h),
            _fmt(ic["pearson"]["mean"], ".4f"),
            _fmt(sp["mean"], ".4f"),
            _fmt(sp["t_stat"], ".2f"),
            _fmt(sp["ir"], ".3f"),
            _fmt(sp["hit_rate"], ".2f"),
            _fmt(qa["spread_mean"], ".4f"),
            _fmt(qa["monotonicity"], ".2f"),
        )
    return table


def _regime_table(sheet: SignalTearSheet) -> Table:
    """The by-regime IC of the headline horizon (Spearman; the t-statistic keeps the gaps)."""
    s = sheet.summary
    h = s["headline_horizon"]
    table = Table(title=f"IC by {s['by_regime']['source']}, {h}-session forward return")
    for name in ("regime", "IC spearman", "NW t", "hit rate", "dates"):
        table.add_column(name, justify="left" if name == "regime" else "right")
    for regime, row in s["by_regime"][f"h{h}"].items():
        table.add_row(
            regime,
            _fmt(row["mean"], ".4f"),
            _fmt(row["t_stat"], ".2f"),
            _fmt(row["hit_rate"], ".2f"),
            str(row["n_dates"]),
        )
    return table


def _refuse_holdout(check: HoldoutCheck) -> typer.BadParameter:
    return typer.BadParameter(
        f"the labels of {check.last_decision} close on {check.last_label_session}, after "
        f"backtest.in_sample_end ({check.in_sample_end}): this window reads the hold-out "
        f"(spec A7). The last --to that stays in sample is {check.latest_decision_in_sample}. "
        "Pass --allow-holdout to run anyway; the override is written to the audit log.",
        param_hint="--to",
    )


@research_app.command("signal")
def signal_cmd(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name", help="A technical feature or fundamental factor.")],
    start: Annotated[str, typer.Option("--from", help="First date: YYYY or YYYY-MM-DD.")],
    end: Annotated[str, typer.Option("--to", help="Last date: YYYY or YYYY-MM-DD.")],
    frequency: Annotated[
        str | None,
        typer.Option(help="Evaluation dates: daily, weekly or monthly (default: config)."),
    ] = None,
    universe: Annotated[
        str | None, typer.Option(help="Universe whose members are evaluated (default: config).")
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option(help="Output directory (default: <lake>/<quant.research.out_dir>/<name>)."),
    ] = None,
    regimes: Annotated[
        str | None,
        typer.Option(help="Add the IC by stored regime: vol, trend or quadrant."),
    ] = None,
    allow_holdout: Annotated[
        bool,
        typer.Option(
            "--allow-holdout",
            help="Run although the labels close after backtest.in_sample_end (audited).",
        ),
    ] = False,
) -> None:
    """Evaluate a signal (IC, quantiles, decay, long-short backtest) and save its tear sheet."""
    console = _console()
    first, last = parse_bound(start), parse_bound(end, end=True)
    try:
        signal_kind(name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--name") from None
    if frequency is not None and frequency not in FREQUENCIES:
        raise typer.BadParameter(
            f"must be one of {', '.join(FREQUENCIES)}", param_hint="--frequency"
        )
    if regimes is not None and regimes not in REGIME_KINDS:
        raise typer.BadParameter(
            f"must be one of {', '.join(REGIME_KINDS)}", param_hint="--regimes"
        )
    if first > last:
        raise typer.BadParameter(f"--from {first} is after --to {last}", param_hint="--from")
    config = _config(ctx, console)
    freq = config.quant.research.frequency if frequency is None else cast(Frequency, frequency)
    dates = evaluation_dates(first, last, freq, TradingCalendar(config=config.calendar))
    if not dates:
        raise typer.BadParameter(
            f"no trading session between {first} and {last}", param_hint="--from"
        )
    check = holdout_check(dates, config=config)
    if check.reads_holdout and not allow_holdout:
        raise _refuse_holdout(check)
    lake = Lake(config.data.lake_path)
    target = (
        out
        if out is not None
        else Path(config.data.lake_path) / config.quant.research.out_dir / name
    )
    views: list[DataView] = []

    def factory(day: date) -> DataView:
        view = DataView(lake, day, config) if not views else views[-1].advance(day)
        views[:] = [view]
        return view

    progress = Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=not console.is_terminal,
    )
    try:
        with run_context(config=config, stage="research.signal") as run, progress:
            configure_logging(
                level="INFO", log_file=target / "research.log", console_level="WARNING"
            )
            if check.reads_holdout:
                _record_holdout_override(config, run.run_id, name, first, last, check)
            labels = None
            if regimes is not None:
                labels = RegimeStore(lake, config).labels(regimes, first, last)
                if labels.empty:
                    raise DataError(
                        f"no stored {REGIME_KINDS[regimes]} between {first} and {last}: run "
                        f"`atlas regime build --from {first} --to {last}` first"
                    )
            task = progress.add_task(f"{name}: signal dates", total=len(dates))
            sheet = research_signal(
                name,
                factory,
                first,
                last,
                config=config,
                frequency=freq,
                universe=universe,
                regimes=labels,
                progress=lambda _day: progress.advance(task),
            )
            written = sheet.save(target)
    except AtlasError as exc:
        console.print(f"[red]Research failed:[/red] {type(exc).__name__}: {exc}")
        raise typer.Exit(1) from None
    finally:
        reset_logging()
    if check.reads_holdout:
        console.print(
            f"[yellow]Hold-out:[/yellow] this run read returns to {check.last_label_session}, "
            f"after backtest.in_sample_end ({check.in_sample_end}); the override is in the "
            f"audit log of run {run.run_id}."
        )
    console.print(_summary_table(sheet))
    if regimes is not None:
        console.print(_regime_table(sheet))
        missing = sheet.summary["by_regime"]["dates_without_regime"]
        if missing:
            console.print(
                f"[yellow]Note:[/yellow] {missing} of {len(dates)} dates have no stored regime."
            )
    bt = sheet.summary["backtest"]
    console.print(
        f"Long-short backtest ({bt['weighting']}, {bt['rebalance']}, {bt['cost_bps']} bps): "
        f"Sharpe {_fmt(bt['sharpe'], '.2f')}, annual return {_fmt(bt['ann_return'], '.2%')}, "
        f"vol {_fmt(bt['ann_vol'], '.2%')}, max drawdown {_fmt(bt['max_drawdown'], '.2%')}, "
        f"annual turnover {_fmt(bt['annual_turnover'], '.1f')}"
    )
    blocked = sheet.summary["labels"]["blocked"]
    if blocked:
        console.print(f"[yellow]Note:[/yellow] {len(blocked)} name(s) had no labels (blocked).")
    console.print(f"Run {run.run_id}. Tear sheet: {target} ({len(written)} files)")


def _record_holdout_override(
    config: AtlasConfig, run_id: str, name: str, first: date, last: date, check: HoldoutCheck
) -> None:
    """Write the `--allow-holdout` override to the run's audit log (needs the run context).
    `AtlasError` when the log cannot be written: an override that leaves no record must not
    run."""
    try:
        AuditLog(audit_path(config.data.lake_path, run_id)).record(
            EventType.OVERRIDE,
            AUDIT_ACTOR,
            {
                "command": "research signal",
                "override": "allow_holdout",
                "signal": name,
                "from": first,
                "to": last,
                "in_sample_end": check.in_sample_end,
                "last_label_session": check.last_label_session,
            },
        )
    except OSError as exc:
        raise AtlasError(
            f"cannot write the hold-out override to the audit log ({exc}); not running"
        ) from exc


def _pairs_table(snap: PairsSnapshot) -> Table:
    table = Table(title=f"Pairs at {snap.as_of.isoformat()}: {len(snap.pairs)} pairs")
    for name in (
        "pair",
        "sector",
        "beta",
        "half-life",
        "hurst",
        "p-value",
        "corr",
        "z",
        "signal",
        "exp return",
        "confidence",
    ):
        table.add_column(name, justify="left" if name in ("pair", "sector", "signal") else "right")
    for row in snap.table.itertuples():
        table.add_row(
            str(row.pair_id),
            str(row.sector),
            _fmt(row.hedge_ratio, ".3f"),
            _fmt(row.half_life, ".1f"),
            _fmt(row.hurst, ".2f"),
            _fmt(row.pvalue, ".4f"),
            _fmt(row.corr, ".2f"),
            _fmt(row.z, "+.2f"),
            str(row.signal),
            _fmt(row.exp_return, ".1%"),
            _fmt(row.confidence, ".2f"),
        )
    return table


@research_app.command("pairs")
def pairs_cmd(
    ctx: typer.Context,
    as_of: Annotated[str, typer.Option("--as-of", help="The as-of date: YYYY-MM-DD.")],
    universe: Annotated[
        str | None, typer.Option(help="Universe whose members are paired (default: config).")
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option(
            help="Output directory (default: <lake>/<quant.research.out_dir>/pairs/<as-of>)."
        ),
    ] = None,
) -> None:
    """List the cointegrated pairs selected at the as-of with their current z-scores."""
    console = _console()
    try:
        day = date.fromisoformat(as_of.strip())
    except ValueError:
        raise typer.BadParameter(f"{as_of!r} is not a date (YYYY-MM-DD)") from None
    config = _config(ctx, console)
    cfg = config.quant.statarb
    if universe is not None:
        if universe not in config.data.universes:
            known = ", ".join(sorted(config.data.universes))
            raise typer.BadParameter(f"unknown universe (known: {known})", param_hint="--universe")
        cfg = cfg.model_copy(update={"universe": universe})
    target = (
        out
        if out is not None
        else Path(config.data.lake_path) / config.quant.research.out_dir / "pairs" / day.isoformat()
    )
    try:
        with run_context(config=config, stage="research.pairs") as run:
            configure_logging(
                level="INFO", log_file=target / "research.log", console_level="WARNING"
            )
            snap = pairs_snapshot(DataView(Lake(config.data.lake_path), day, config), cfg)
            target.mkdir(parents=True, exist_ok=True)
            snap.table.to_csv(target / "pairs.csv", index=False)
            summary = {
                "as_of": day.isoformat(),
                "universe": cfg.universe,
                "n_pairs": len(snap.pairs),
                "run": {
                    "run_id": run.run_id,
                    "stage": run.stage,
                    "mode": run.mode,
                    "config_fingerprint": run.config_fingerprint,
                },
                "signals": [s.model_dump() for s in snap.signals],
                "config": cfg.model_dump(),
            }
            (target / "pairs.json").write_text(json.dumps(summary, indent=2) + "\n")
    except AtlasError as exc:
        console.print(f"[red]Pairs failed:[/red] {type(exc).__name__}: {exc}")
        raise typer.Exit(1) from None
    finally:
        reset_logging()
    if snap.pairs:
        console.print(_pairs_table(snap))
        console.print(
            f"{len(snap.signals)} of {len(snap.pairs)} pairs would enter now "
            f"(|z| > {cfg.entry_z}, stateless snapshot)."
        )
    else:
        console.print(f"No pairs selected at {day.isoformat()} (universe {cfg.universe}).")
    console.print(f"Run {run.run_id}. Output: {target}")


__all__ = ["pairs_cmd", "parse_bound", "research_app", "signal_cmd"]
