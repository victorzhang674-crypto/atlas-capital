"""`atlas data …`: backfill, update and inspect the data lake (Stage 14).

`sync` backfills (resumable), `update` fetches what is new since the last sync, `status`
summarises the lake and `quality` runs the Stage 13 quality report. `sync`, `update` and
`quality --persist` open a run: a JSON log at `<lake>/runs/<run_id>/<command>.log` and the
run's audit log (`RUN_STARTED`, one `DATA_SYNC` per step, `RUN_FINISHED`). `status` and
`quality` without `--persist` only read. Exit codes: 0 ok, 1 a step or the command failed,
2 bad arguments.
"""

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any

import pandas as pd
import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from atlas.core.audit import AuditLog, EventType, audit_path
from atlas.core.clock import Clock, LiveClock, get_clock, use_clock
from atlas.core.config import AtlasConfig, load_config
from atlas.core.errors import ConfigError, DataError
from atlas.core.logging import configure_logging, reset_logging
from atlas.core.run_context import run_context, run_dir
from atlas.core.storage import Lake
from atlas.data.providers.base import check_request
from atlas.data.quality import blocked_symbols, quality_report
from atlas.data.sync import (
    LakeStatus,
    StepReport,
    SyncReport,
    SyncSources,
    lake_status,
    next_decision_day,
    parse_datasets,
    run_sync,
    run_update,
)

data_app = typer.Typer(
    help="Backfill, update and inspect the data lake.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)

AUDIT_ACTOR = "cli"
"""`actor` of the `RUN_STARTED` / `RUN_FINISHED` events."""

build_sources: Callable[[AtlasConfig, Lake], SyncSources] = SyncSources.live
"""Builds the sync's sources (tests replace it with fakes)."""

make_clock: Callable[[], Clock] = LiveClock
"""Builds the clock the commands run under (tests replace it with a `SimClock`)."""

_DATE = ["%Y-%m-%d"]
FromOpt = Annotated[
    datetime | None,
    typer.Option("--from", formats=_DATE, help="First date (default: data.sync.default_start)."),
]
ToOpt = Annotated[
    datetime | None, typer.Option("--to", formats=_DATE, help="Last date (default: today).")
]
DatasetsOpt = Annotated[
    str | None,
    typer.Option(
        help="Comma-separated datasets: universe, prices, fundamentals, macro, news, events, "
        "sentiment, quality (default: all)."
    ),
]
SymbolsOpt = Annotated[
    str | None,
    typer.Option(
        help="Comma-separated tickers replacing the default list (index members in the "
        "window plus the configured ETFs)."
    ),
]


CONSOLE_MIN_WIDTH = 160
"""Width used when output is not a terminal (logs, pipes): Rich would fall back to 80 columns
and abbreviate the summary tables."""


def _console() -> Console:
    console = Console(soft_wrap=False, highlight=False)
    if not console.is_terminal and console.width < CONSOLE_MIN_WIDTH:
        console = Console(soft_wrap=False, highlight=False, width=CONSOLE_MIN_WIDTH)
    return console


def _options(ctx: typer.Context) -> tuple[str, Path]:
    obj = ctx.find_root().obj
    return getattr(obj, "profile", "dev"), Path(getattr(obj, "config_dir", Path("config")))


def _config(ctx: typer.Context, console: Console) -> AtlasConfig:
    profile, config_dir = _options(ctx)
    try:
        return load_config(profile, config_dir=config_dir)
    except ConfigError as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        raise typer.Exit(1) from None


def _datasets(raw: str | None) -> tuple[str, ...] | None:
    if raw is None:
        return None
    try:
        return parse_datasets(raw.split(","))
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--datasets") from None


def _symbols(raw: str | None) -> tuple[str, ...] | None:
    if raw is None:
        return None
    names = [s.strip() for s in raw.split(",") if s.strip()]
    if not names:
        raise typer.BadParameter("no symbols given", param_hint="--symbols")
    try:
        return check_request(names, date.min, date.min)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--symbols") from None


@dataclass(frozen=True)
class _RunHandle:
    config: AtlasConfig
    lake: Lake
    audit: AuditLog
    run_id: str


@contextmanager
def _run(
    config: AtlasConfig, command: str, args: dict[str, Any], console: Console
) -> Iterator[_RunHandle]:
    """A top-level run for a writing command: clock, run context, JSON log file (console:
    warnings only), audit log with `RUN_STARTED` / `RUN_FINISHED` (also when it crashes)."""
    lake = Lake(config.data.lake_path)
    with use_clock(make_clock()), run_context(config=config, stage=f"data.{command}") as ctx:
        folder = run_dir(config.data.lake_path, ctx.run_id)
        configure_logging(level="INFO", log_file=folder / f"{command}.log", console_level="WARNING")
        audit = AuditLog(audit_path(config.data.lake_path, ctx.run_id))
        audit.record(EventType.RUN_STARTED, AUDIT_ACTOR, {"command": f"data {command}", **args})
        handle = _RunHandle(config, lake, audit, ctx.run_id)
        finished: dict[str, Any] = {"command": f"data {command}", "ok": False, "crashed": None}
        try:
            yield handle
            finished["ok"] = True
        except typer.Exit as exc:
            finished["ok"] = exc.exit_code == 0
            raise
        except BaseException as exc:
            finished["crashed"] = f"{type(exc).__name__}: {exc}"
            console.print(f"[red]Stopped:[/red] {finished['crashed']} (finished chunks are kept)")
            raise
        finally:
            audit.record(EventType.RUN_FINISHED, AUDIT_ACTOR, finished)
            reset_logging()
            console.print(f"Run {ctx.run_id}: log and audit trail in {folder}")


class RichProgress:
    """`SyncProgress` drawn as one Rich progress bar per step (thread-safe)."""

    def __init__(self, progress: Progress) -> None:
        self._progress = progress
        self._tasks: dict[str, TaskID] = {}
        self._lock = threading.Lock()

    def start_step(self, step: str, total: int) -> None:
        """Add a bar for `step` with `total` keys."""
        with self._lock:
            self._tasks[step] = self._progress.add_task(step, total=max(total, 1))
            if total == 0:
                self._progress.update(self._tasks[step], completed=1)

    def advance(self, step: str, n: int) -> None:
        """Move `step`'s bar on by `n` keys."""
        with self._lock:
            task = self._tasks.get(step)
        if task is not None:
            self._progress.advance(task, n)

    def end_step(self, report: StepReport) -> None:
        """Fill `step`'s bar and label it with the outcome."""
        with self._lock:
            task = self._tasks.get(report.step)
            if task is None:
                task = self._progress.add_task(report.step, total=1)
                self._tasks[report.step] = task
        label = f"{report.step} [red]error[/red]" if report.error else report.step
        total = self._progress.tasks[task].total or 1
        self._progress.update(task, completed=total, description=label)


def _progress(console: Console) -> Progress:
    return Progress(
        TextColumn("{task.description:<16}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )


def _summary(report: SyncReport) -> Table:
    table = Table(title=f"Sync summary ({report.command} {report.start} .. {report.end})")
    for name in ("step", "keys", "skipped", "ok", "empty", "failed", "rows", "chunks", "seconds"):
        table.add_column(name, justify="left" if name == "step" else "right")
    table.add_column("note")
    for step in report.steps:
        sample = ", ".join(f"{k}: {v}" for k, v in list(step.failed.items())[:3])
        note = step.error or (sample + (" …" if len(step.failed) > 3 else ""))
        table.add_row(
            step.step,
            str(step.keys),
            str(step.skipped),
            str(step.ok),
            str(step.empty),
            str(len(step.failed)),
            f"{step.rows:,}",
            str(step.chunks),
            f"{step.seconds:.1f}",
            note,
        )
    return table


def _finish(report: SyncReport, console: Console) -> None:
    console.print(_summary(report))
    if not report.ok:
        for step in report.errors:
            console.print(f"[red]{step.step} could not run:[/red] {step.error}")
        raise typer.Exit(1)


@data_app.command()
def sync(
    ctx: typer.Context,
    from_: FromOpt = None,
    to: ToOpt = None,
    datasets: DatasetsOpt = None,
    symbols: SymbolsOpt = None,
) -> None:
    """Backfill the lake for a date window (resumes an interrupted backfill)."""
    console = _console()
    chosen = _datasets(datasets)
    wanted = _symbols(symbols)
    start = from_.date() if from_ is not None else None
    end = to.date() if to is not None else None
    config = _config(ctx, console)
    first = start if start is not None else config.data.sync.default_start
    if end is not None and first > end:
        raise typer.BadParameter(f"--from {first} is after --to {end}", param_hint="--from")
    with use_clock(make_clock()):
        last = end if end is not None else get_clock().today()
    if first > last:
        raise typer.BadParameter(f"--from {first} is after today ({last})", param_hint="--from")
    args = {
        "start": first.isoformat(),
        "end": last.isoformat(),
        "datasets": list(chosen) if chosen is not None else None,
        "symbols": list(wanted) if wanted is not None else None,
    }
    with _run(config, "sync", args, console) as run, _progress(console) as bars:
        report = run_sync(
            run.lake,
            config,
            build_sources(config, run.lake),
            start=first,
            end=last,
            datasets=chosen,
            symbols=wanted,
            progress=RichProgress(bars),
            audit=run.audit,
        )
    _finish(report, console)


@data_app.command()
def update(
    ctx: typer.Context,
    datasets: DatasetsOpt = None,
    symbols: SymbolsOpt = None,
) -> None:
    """Fetch what is new since the last sync (each key from its last covered date)."""
    console = _console()
    chosen = _datasets(datasets)
    wanted = _symbols(symbols)
    config = _config(ctx, console)
    args = {
        "datasets": list(chosen) if chosen is not None else None,
        "symbols": list(wanted) if wanted is not None else None,
    }
    with _run(config, "update", args, console) as run:
        try:
            with _progress(console) as bars:
                report = run_update(
                    run.lake,
                    config,
                    build_sources(config, run.lake),
                    datasets=chosen,
                    symbols=wanted,
                    progress=RichProgress(bars),
                    audit=run.audit,
                )
        except DataError as exc:
            console.print(f"[red]Cannot update:[/red] {exc}")
            raise typer.Exit(1) from None
    _finish(report, console)


def _date_text(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NaT:
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M UTC")
    return str(value)


def _status_tables(status: LakeStatus) -> list[Table]:
    tables = Table(title=f"Lake tables on {status.as_of}")
    for name in ("table", "dataset", "rows", "keys", "first", "last", "last sync"):
        tables.add_column(name, justify="right" if name in ("rows", "keys") else "left")
    for row in status.tables.itertuples(index=False):
        tables.add_row(
            str(row.table),
            str(row.dataset),
            f"{row.rows:,}",
            f"{row.keys:,}",
            _date_text(row.first),
            _date_text(row.last),
            _date_text(row.last_sync),
        )
    coverage = Table(title=f"Coverage ({status.members} current members)")
    for name in ("dataset", "rule", "covered", "pct", "missing (first 10)"):
        coverage.add_column(name, justify="right" if name in ("covered", "pct") else "left")
    for line in status.coverage:
        pct = "-" if pd.isna(line.pct) else f"{line.pct:.1f}%"
        missing = ", ".join(line.missing[:10]) + (" …" if len(line.missing) > 10 else "")
        coverage.add_row(line.dataset, line.rule, f"{line.covered}/{line.total}", pct, missing)
    return [tables, coverage]


@data_app.command()
def status(ctx: typer.Context) -> None:
    """Row counts, date ranges, last sync, coverage and open quality issues (read-only)."""
    console = _console()
    config = _config(ctx, console)
    lake = Lake(config.data.lake_path)
    with use_clock(make_clock()):
        summary = lake_status(lake, config)
    for table in _status_tables(summary):
        console.print(table)
    if summary.note:
        console.print(f"[yellow]Note:[/yellow] {summary.note}")
    if summary.quality_day is None:
        console.print("Quality: no persisted report yet (run atlas data sync or quality --persist)")
    else:
        counts = ", ".join(
            f"{s} {n}"
            for s, n in zip(summary.quality["severity"], summary.quality["issues"], strict=True)
        )
        blocked = ", ".join(summary.blocked) or "none"
        console.print(
            f"Quality report {summary.quality_day}: {counts or 'no issues'}; blocked: {blocked}"
        )
    if summary.last_run is not None:
        run = summary.last_run
        outcome = (
            "crashed"
            if run.get("crashed")
            else ("errors " + ", ".join(run["errors"]) if run.get("errors") else "ok")
        )
        console.print(
            f"Last run: {run.get('command')} {run.get('start')} .. {run.get('end')} "
            f"finished {run.get('finished_at')} ({outcome})"
        )


@data_app.command()
def quality(
    ctx: typer.Context,
    as_of: Annotated[
        datetime | None,
        typer.Option(formats=_DATE, help="Trading day (default: the next decision day)."),
    ] = None,
    persist: Annotated[bool, typer.Option(help="Store the report in data_quality_issues.")] = False,
    limit: Annotated[int, typer.Option(min=0, help="Issues listed (most severe first).")] = 20,
) -> None:
    """Run the data quality report for a decision day (Stage 13 checks)."""
    console = _console()
    config = _config(ctx, console)

    def report(lake: Lake) -> None:
        day = as_of.date() if as_of is not None else next_decision_day(config)
        issues = quality_report(lake, day, config=config, persist=persist)
        _print_quality(console, day, issues, limit)

    if persist:
        with _run(
            config, "quality", {"as_of": as_of.date().isoformat() if as_of else None}, console
        ) as run:
            report(run.lake)
    else:
        with use_clock(make_clock()):
            report(Lake(config.data.lake_path))


def _print_quality(console: Console, day: date, issues: pd.DataFrame, limit: int) -> None:
    console.print(f"Quality report for {day}: {len(issues)} issue(s)")
    if issues.empty:
        return
    counts = issues.groupby(["check", "severity"]).size().reset_index(name="issues")
    by_check = Table(title="Issues by check")
    for name in ("check", "severity", "issues"):
        by_check.add_column(name)
    for row in counts.itertuples(index=False):
        by_check.add_row(str(row.check), str(row.severity), str(row.issues))
    console.print(by_check)
    rank = {"critical": 0, "warning": 1, "info": 2}
    top = issues.assign(_rank=issues["severity"].map(rank)).sort_values(["_rank", "symbol", "date"])
    listing = Table(title=f"First {min(limit, len(top))} issue(s)")
    for name in ("severity", "table", "symbol", "date", "check", "detail"):
        listing.add_column(name)
    for row in top.head(limit).itertuples(index=False):
        listing.add_row(
            str(row.severity),
            str(row.table),
            str(row.symbol),
            str(row.date),
            str(row.check),
            str(row.detail),
        )
    console.print(listing)
    blocked = ", ".join(sorted(blocked_symbols(issues))) or "none"
    console.print(f"Blocked symbols (critical issues): {blocked}")


__all__ = ["RichProgress", "build_sources", "data_app", "make_clock"]
