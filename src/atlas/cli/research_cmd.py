"""`atlas research …`: signal research (Stage 17).

`atlas research signal --name momentum_12_1 --from 2016 --to 2021` evaluates a Stage 15
technical feature or a Stage 16 fundamental factor on the evaluation dates in the window
(point in time: one `DataView` per date, the universe's members at that date), against
forward returns, and writes its tear sheet (`tearsheet.json`, CSVs, PNG figures) to
`<lake>/<quant.research.out_dir>/<name>/` or `--out`. It only reads the lake. A bare year
means 1 January (`--from`) or 31 December (`--to`). Exit codes: 0 ok, 1 the run failed,
2 bad arguments.
"""

import re
from datetime import date
from pathlib import Path
from typing import Annotated, Any, cast

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig, load_config
from atlas.core.errors import AtlasError, ConfigError
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.quant.research import (
    FREQUENCIES,
    Frequency,
    SignalTearSheet,
    evaluation_dates,
    research_signal,
    signal_kind,
)

research_app = typer.Typer(
    help="Signal research: information coefficients, quantiles, decay, long-short backtest.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)

CONSOLE_MIN_WIDTH = 160
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
    table = Table(
        title=f"{s['signal']} ({s['kind']}): {s['start']} .. {s['end']}, {s['n_dates']} dates"
    )
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
    if first > last:
        raise typer.BadParameter(f"--from {first} is after --to {last}", param_hint="--from")
    config = _config(ctx, console)
    freq = config.quant.research.frequency if frequency is None else cast(Frequency, frequency)
    dates = evaluation_dates(first, last, freq, TradingCalendar(config=config.calendar))
    if not dates:
        raise typer.BadParameter(
            f"no trading session between {first} and {last}", param_hint="--from"
        )
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
        with progress:
            task = progress.add_task(f"{name}: signal dates", total=len(dates))
            sheet = research_signal(
                name,
                factory,
                first,
                last,
                config=config,
                frequency=freq,
                universe=universe,
                progress=lambda _day: progress.advance(task),
            )
        written = sheet.save(target)
    except AtlasError as exc:
        console.print(f"[red]Research failed:[/red] {type(exc).__name__}: {exc}")
        raise typer.Exit(1) from None
    console.print(_summary_table(sheet))
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
    console.print(f"Tear sheet: {target} ({len(written)} files)")


__all__ = ["parse_bound", "research_app", "signal_cmd"]
