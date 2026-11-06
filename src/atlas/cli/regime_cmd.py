"""`atlas regime …`: market-regime history (Stage 21).

`atlas regime build --from 2016-01-04 --to 2026-11-05` computes the regime of every decision
day in the window (`regime_history`: point in time, one `DataView`) from the data lake
(`data.lake_path`) and stores it in the `regime_history` table of `--store` (default: the data
lake), replacing the stored rows of the same days. `--to` is clipped to the last decision day
whose previous session has a benchmark bar. A bare year means 1 January (`--from`) or
31 December (`--to`). It runs inside a `run_context` (stage `regime.build`); the log goes to
`<store>/runs/regime/build.log` (JSON lines), the console shows warnings and errors only.
Exit codes: 0 ok, 1 the build failed, 2 bad arguments.
"""

from collections import Counter
from datetime import date
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from atlas.cli.research_cmd import parse_bound
from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig, load_config
from atlas.core.errors import AtlasError, ConfigError, DataError
from atlas.core.logging import configure_logging, reset_logging
from atlas.core.run_context import run_context
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.quant.regime import VOL_LABELS, RegimeState, RegimeStore, regime_history

regime_app = typer.Typer(
    help="Market regimes: build and store the daily regime history.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)


def _config(ctx: typer.Context, console: Console) -> AtlasConfig:
    obj = ctx.find_root().obj
    profile = getattr(obj, "profile", "dev")
    config_dir = Path(getattr(obj, "config_dir", Path("config")))
    try:
        return load_config(profile, config_dir=config_dir)
    except ConfigError as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        raise typer.Exit(1) from None


def last_decision(lake: Lake, first: date, last: date, config: AtlasConfig) -> date:
    """The last decision day in [`first`, `last`] whose previous session has a benchmark bar
    (`DataError` when the benchmark has no bar in the window)."""
    cal = TradingCalendar(config=config.calendar)
    day = last if cal.is_session(last) else cal.offset(cal.next_session(last), -1)
    bench = config.fund.benchmark
    n = max(len(cal.sessions(first, day)), 1)
    bars = DataView(lake, day, config).prices([bench], n)
    if bars.empty:
        raise DataError(f"no bars for the benchmark {bench} between {first} and {day}")
    return min(day, cal.next_session(max(bars["date"])))


def _summary(states: list[RegimeState]) -> Table:
    table = Table(title="Regime history by year")
    for name in ("year", "days", *VOL_LABELS, "DOWN", "stress", "mean scalar"):
        table.add_column(name, justify="right")
    for year in sorted({s.as_of.year for s in states}):
        ys = [s for s in states if s.as_of.year == year]
        vol = Counter(s.vol_regime for s in ys)
        table.add_row(
            str(year),
            str(len(ys)),
            *(str(vol.get(label, 0)) for label in VOL_LABELS),
            str(sum(s.trend_regime == "DOWN" for s in ys)),
            str(sum(s.stress_flag for s in ys)),
            f"{sum(s.risk_scalar for s in ys) / len(ys):.3f}",
        )
    return table


@regime_app.command("build")
def build_cmd(
    ctx: typer.Context,
    start: Annotated[str, typer.Option("--from", help="First decision day: YYYY or YYYY-MM-DD.")],
    end: Annotated[str, typer.Option("--to", help="Last decision day: YYYY or YYYY-MM-DD.")],
    store: Annotated[
        Path | None,
        typer.Option(help="Lake that receives the regime_history table (default: the data lake)."),
    ] = None,
) -> None:
    """Compute the daily regime history and store it (`regime_history` table)."""
    console = Console(soft_wrap=False, highlight=False)
    first, last = parse_bound(start), parse_bound(end, end=True)
    if first > last:
        raise typer.BadParameter(f"--from {first} is after --to {last}", param_hint="--from")
    config = _config(ctx, console)
    lake = Lake(config.data.lake_path)
    target = Lake(store) if store is not None else lake
    try:
        with run_context(config=config, stage="regime.build") as run:
            configure_logging(
                level="INFO",
                log_file=target.runs_dir / "regime" / "build.log",
                console_level="WARNING",
            )
            stop = last_decision(lake, first, last, config)
            states = regime_history(lake, first, stop, config)
            written = RegimeStore(target, config).write(states)
    except (AtlasError, ValueError) as exc:
        console.print(f"[red]Regime build failed:[/red] {type(exc).__name__}: {exc}")
        raise typer.Exit(1) from None
    finally:
        reset_logging()
    console.print(_summary(states))
    console.print(
        f"Regime history: {len(states)} decision days, starts {states[0].as_of}, ends "
        f"{states[-1].as_of}; {written} rows stored in {target.root} (regime_history). "
        f"Run {run.run_id}."
    )


__all__ = ["build_cmd", "last_decision", "regime_app"]
