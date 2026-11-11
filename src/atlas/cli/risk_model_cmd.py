"""`atlas risk-model …`: the factor risk model's stored history (Stage 19; command added by
the Phase 2 checkpoint).

`atlas risk-model build --from 2017 --to 2026` fits the daily cross-sectional regressions over
the window from the data lake (`data.lake_path`; point in time, through `DataView`) and stores
the daily factor returns (`factor_returns`) and a model snapshot on each month-end with enough
history (`risk_model_snapshots`) in `--store` (default: the data lake), replacing stored rows
of the same days. With `--etfs` (the default) each snapshot also carries the time-series
exposures of the ETFs in `quant.risk_model.factor_model.etf.universe`. `--to` is clipped to
the last session with a benchmark bar. A bare year means 1 January (`--from`) or 31 December
(`--to`). It runs inside a `run_context` (stage `risk_model.build`); the log goes to
`<store>/runs/risk_model/build.log` (JSON lines: one line per fundamental anchor loaded and per
snapshot stored), the console shows warnings and errors only.
Exit codes: 0 ok, 1 the build failed, 2 bad arguments.

All stored numbers are daily (factor returns as simple daily returns, covariances per
session); a snapshot's date is its data date (the last session included).
"""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from atlas.cli.regime_cmd import last_decision
from atlas.cli.research_cmd import parse_bound
from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig, load_config
from atlas.core.errors import AtlasError, ConfigError
from atlas.core.logging import configure_logging, reset_logging
from atlas.core.run_context import run_context
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.quant.risk_model.loader import build_and_store
from atlas.quant.risk_model.store import RiskModelStore

risk_model_app = typer.Typer(
    help="Factor risk model: build and store daily factor returns and month-end snapshots.",
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


@risk_model_app.command("build")
def build_cmd(
    ctx: typer.Context,
    start: Annotated[str, typer.Option("--from", help="First session: YYYY or YYYY-MM-DD.")],
    end: Annotated[str, typer.Option("--to", help="Last session: YYYY or YYYY-MM-DD.")],
    store: Annotated[
        Path | None,
        typer.Option(
            help="Lake that receives factor_returns and risk_model_snapshots "
            "(default: the data lake)."
        ),
    ] = None,
    etfs: Annotated[
        bool,
        typer.Option(
            "--etfs/--no-etfs", help="Add the configured ETF universe's exposures to snapshots."
        ),
    ] = True,
) -> None:
    """Fit the daily factor returns and store them with the month-end model snapshots."""
    console = Console(soft_wrap=False, highlight=False)
    first, last = parse_bound(start), parse_bound(end, end=True)
    if first > last:
        raise typer.BadParameter(f"--from {first} is after --to {last}", param_hint="--from")
    config = _config(ctx, console)
    lake = Lake(config.data.lake_path)
    target = RiskModelStore(Lake(store) if store is not None else lake, config)
    try:
        with run_context(config=config, stage="risk_model.build") as run:
            configure_logging(
                level="INFO",
                log_file=target.lake.runs_dir / "risk_model" / "build.log",
                console_level="WARNING",
            )
            cal = TradingCalendar(config=config.calendar)
            wanted = last if cal.is_session(last) else cal.prev_session(last)
            # the decision morning after the last session to regress: its bars must be known
            decision = last_decision(lake, first, cal.next_session(wanted), config)
            stop = cal.offset(decision, -1)  # `--to`, or the last session with a benchmark bar
            names = (
                list(
                    DataView(lake, decision, config).universe(
                        config.quant.risk_model.factor_model.etf.universe
                    )
                )
                if etfs
                else None
            )
            history, snapshots = build_and_store(lake, target, first, stop, config, etfs=names)
    except (AtlasError, ValueError) as exc:
        console.print(f"[red]Risk model build failed:[/red] {type(exc).__name__}: {exc}")
        raise typer.Exit(1) from None
    finally:
        reset_logging()
    index = history.factor_returns.index
    span = f"from {index[0].date()} to {index[-1].date()}" if len(index) else "none"
    shots = (
        f"{len(snapshots)} snapshots from {snapshots[0]} to {snapshots[-1]}"
        if snapshots
        else "0 snapshots (not enough history in the window)"
    )
    console.print(
        f"Risk model: {len(index)} sessions fitted {span}, {len(history.skipped)} skipped "
        f"(too few names), mean R2 {float(history.r2.mean()):.3f}, mean names "
        f"{float(history.n_names.mean()):.0f}; {shots}"
        f"{f' with {len(names)} ETFs' if names else ''}; tables factor_returns and "
        f"risk_model_snapshots in {target.lake.root} (settings {target.config_hash}). "
        f"Run {run.run_id}."
    )


__all__ = ["build_cmd", "risk_model_app"]
