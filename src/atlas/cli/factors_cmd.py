"""`atlas factors …`: the fundamental factor cache (Stage 16; command added by the Phase 2
checkpoint).

`atlas factors build --from 2015 --to 2026` computes the Stage 16 factor panel on the last
trading session of each month in the window (`monthly_rebalance_dates`; point in time, one
`DataView` advanced month by month) from the data lake (`data.lake_path`) and stores it in the
`factor_exposures` table of `--store` (default: the data lake). Month-ends already stored with
the current factor settings are skipped unless `--refresh`. `--to` is clipped to the last
decision day whose previous session has a benchmark bar. A bare year means 1 January
(`--from`) or 31 December (`--to`). It runs inside a `run_context` (stage `factors.build`);
the log goes to `<store>/runs/factors/build.log` (JSON lines, one `factors.cache_written` line
per month-end), the console shows warnings and errors only.
Exit codes: 0 ok, 1 the build failed, 2 bad arguments.
"""

from datetime import date
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from atlas.cli.regime_cmd import last_decision
from atlas.cli.research_cmd import parse_bound
from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig, load_config
from atlas.core.errors import AtlasError, ConfigError, DataError
from atlas.core.logging import configure_logging, reset_logging
from atlas.core.run_context import run_context
from atlas.core.storage import Lake
from atlas.quant.factors import FactorStore, build_factor_cache, monthly_rebalance_dates

factors_app = typer.Typer(
    help="Fundamental factors: build and store the month-end factor panels.",
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


def month_ends(lake: Lake, first: date, last: date, config: AtlasConfig) -> tuple[date, ...]:
    """The month-end sessions in [`first`, `last`] the lake has data for: `last` is clipped to
    the last decision day whose previous session has a benchmark bar. `DataError` when the
    window holds none."""
    stop = last_decision(lake, first, last, config)
    cal = TradingCalendar(config=config.calendar)
    days = tuple(d for d in monthly_rebalance_dates(first, max(first, stop), cal) if d <= stop)
    if not days:
        raise DataError(f"no month-end session with data between {first} and {last}")
    return days


def _summary(store: FactorStore, days: tuple[date, ...]) -> Table:
    """Names and the share of sector-median fills per stored month-end, by year."""
    table = Table(title="Factor cache by year")
    for name in ("year", "month-ends", "mean names", "mean filled share"):
        table.add_column(name, justify="right")
    by_year: dict[int, list[tuple[int, float]]] = {}
    for day in days:
        panel = store.read(day)
        if panel is None or panel.date != day:
            continue
        filled = float(panel.filled.to_numpy(dtype=bool).mean()) if panel.filled.size else 0.0
        by_year.setdefault(day.year, []).append((len(panel.exposures), filled))
    for year, rows in sorted(by_year.items()):
        table.add_row(
            str(year),
            str(len(rows)),
            f"{sum(n for n, _ in rows) / len(rows):.0f}",
            f"{sum(f for _, f in rows) / len(rows):.3f}",
        )
    return table


@factors_app.command("build")
def build_cmd(
    ctx: typer.Context,
    start: Annotated[str, typer.Option("--from", help="First date: YYYY or YYYY-MM-DD.")],
    end: Annotated[str, typer.Option("--to", help="Last date: YYYY or YYYY-MM-DD.")],
    store: Annotated[
        Path | None,
        typer.Option(
            help="Lake that receives the factor_exposures table (default: the data lake)."
        ),
    ] = None,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Recompute month-ends that are already stored.")
    ] = False,
) -> None:
    """Compute the month-end factor panels and store them (`factor_exposures` table)."""
    console = Console(soft_wrap=False, highlight=False)
    first, last = parse_bound(start), parse_bound(end, end=True)
    if first > last:
        raise typer.BadParameter(f"--from {first} is after --to {last}", param_hint="--from")
    config = _config(ctx, console)
    lake = Lake(config.data.lake_path)
    target = FactorStore(Lake(store) if store is not None else lake, config, data_lake=lake)
    try:
        with run_context(config=config, stage="factors.build") as run:
            configure_logging(
                level="INFO",
                log_file=target.lake.runs_dir / "factors" / "build.log",
                console_level="WARNING",
            )
            days = month_ends(lake, first, last, config)
            written = build_factor_cache(lake, days, config=config, refresh=refresh, store=target)
    except (AtlasError, ValueError) as exc:
        console.print(f"[red]Factor build failed:[/red] {type(exc).__name__}: {exc}")
        raise typer.Exit(1) from None
    finally:
        reset_logging()
    console.print(_summary(target, days))
    console.print(
        f"Factor cache: {len(days)} month-ends from {days[0]} to {days[-1]}; "
        f"{len(written)} written, {len(days) - len(written)} already stored; table "
        f"factor_exposures in {target.lake.root} (settings {target.config_hash}). "
        f"Run {run.run_id}."
    )


__all__ = ["build_cmd", "factors_app", "month_ends"]
