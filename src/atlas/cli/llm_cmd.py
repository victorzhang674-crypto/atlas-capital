"""`atlas llm …`: the LLM record/replay cache (Stage 25) and the usage ledger (Stage 24).

- `atlas llm usage` shows the spend in the usage ledger (`llm_usage`): one total, then calls,
  cache hits, paid calls, tokens and USD per UTC billing day, run, agent and `provider/model`.
  `--run ID` and `--day YYYY-MM-DD` keep only that run's or that day's rows; `--by` (repeatable:
  day, run, agent, model) chooses the tables (default: all four).
- `atlas llm cache stats` shows what is stored (entries, size, entries per model, oldest and
  newest recording) and the hit rate of the calls in the usage ledger (`llm_usage`).
- `atlas llm cache export OUT.jsonl` writes every entry as one JSON line (oldest first): `key`,
  `created_at`, `provider`, `model`, `request`, `response` and `usage` (tokens and `cost_usd`
  of the recorded call). An existing file is only replaced with `--force`.
- `atlas llm cache import FILE.jsonl` stores the entries of an export file (a recorded live
  run kept as a fixture, or a cache moved from another lake); every line is checked (its key
  must be the SHA-256 of its request) and a file with a bad line stores nothing. Entries
  already stored are kept unless `--replace`. The store is created when it has no cache.
- `atlas llm cache prune --older-than 30d` deletes the entries recorded more than 30 days (or
  `12h` hours) ago, or before a `YYYY-MM-DD` date (UTC); `--dry-run` only counts them.

All of them use the lake given by `--store` (default: the data lake, `data.lake_path`); only
`import` creates one. Exit codes: 0 ok, 1 no cache or ledger in the store / configuration
error / file exists / a damaged import file, 2 bad arguments.
"""

import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import duckdb
import typer
from rich.console import Console
from rich.table import Table

from atlas.core.clock import LiveClock
from atlas.core.config import load_config
from atlas.core.errors import AtlasError, ConfigError, LLMError
from atlas.core.storage import Database, Lake
from atlas.core.storage.db import DB_FILENAME, LLM_CACHE_TABLE, LLM_USAGE_TABLE
from atlas.llm.cache import LLMCache, read_export, write_export
from atlas.llm.cost import USAGE_DIMENSIONS, CostLedger, UsageGroup

llm_app = typer.Typer(
    help="LLM tools: spend from the usage ledger, the record/replay cache.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
cache_app = typer.Typer(
    help="The LLM record/replay cache: stats, export, import, prune.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
llm_app.add_typer(cache_app, name="cache")

_AGE_RE = re.compile(r"^(\d+)([dh])$")

StoreOption = Annotated[
    Path | None,
    typer.Option(help="Lake that holds the cache (default: the data lake, `data.lake_path`)."),
]


def parse_older_than(text: str, now: datetime) -> datetime:
    """The cutoff instant of an `--older-than` value: `30d` (days) or `12h` (hours) before
    `now`, or `YYYY-MM-DD` (that day's start, UTC). Raises `ValueError` for anything else and
    for an age of zero."""
    value = text.strip()
    match = _AGE_RE.fullmatch(value)
    if match is not None:
        amount, unit = int(match.group(1)), match.group(2)
        if amount == 0:
            raise ValueError("the age must be at least 1")
        return now - (timedelta(days=amount) if unit == "d" else timedelta(hours=amount))
    try:
        return datetime.combine(date.fromisoformat(value), datetime.min.time(), tzinfo=UTC)
    except ValueError:
        raise ValueError(
            f"{text!r} is not an age like 30d or 12h, or a date like 2026-11-01"
        ) from None


def _open(
    ctx: typer.Context,
    store: Path | None,
    console: Console,
    *,
    read_only: bool,
    create: bool = False,
    what: str = "LLM cache",
    tables: tuple[str, ...] = (LLM_CACHE_TABLE,),
) -> Database:
    """The database of `store` (or of the configured data lake); exit 1 when it has none,
    unless `create` (then a missing lake and database are created). `what` names the thing
    looked for in the error messages. A read-only open also exits 1 when one of `tables` is
    missing: a database made before Stages 24 and 25 does not have them, and only a writable
    open creates them."""
    root = store
    if root is None:
        obj = ctx.find_root().obj
        profile = getattr(obj, "profile", "dev")
        config_dir = Path(getattr(obj, "config_dir", Path("config")))
        try:
            root = load_config(profile, config_dir=config_dir).data.lake_path
        except ConfigError as exc:
            console.print(f"[red]Configuration error:[/red] {exc}")
            raise typer.Exit(1) from None
    if not create and not (Path(root) / DB_FILENAME).is_file():
        console.print(f"[red]No {what}:[/red] {Path(root) / DB_FILENAME} does not exist.")
        raise typer.Exit(1)
    try:
        database = Database(Lake(root), read_only=read_only)
    except (AtlasError, duckdb.Error) as exc:  # corrupt, or held open by another process
        console.print(f"[red]Cannot open the {what.removeprefix('LLM ')}:[/red] {exc}")
        raise typer.Exit(1) from None
    if read_only:
        present = set(
            database.query(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            )["table_name"]
        )
        missing = [table for table in tables if table not in present]
        if missing:
            database.close()
            noun = "table" if len(missing) == 1 else "tables"
            console.print(
                f"[red]No {what}:[/red] {database.path} has no {' or '.join(missing)} {noun}."
            )
            raise typer.Exit(1)
    return database


class UsageBy(StrEnum):
    """The `--by` choices of `atlas llm usage` (`atlas.llm.cost.USAGE_DIMENSIONS`)."""

    day = "day"
    run = "run"
    agent = "agent"
    model = "model"


def _usage_table(title: str, first: str, groups: tuple[UsageGroup, ...]) -> Table:
    table = Table(title=title)
    table.add_column(first, justify="left")
    for column in ("calls", "paid", "hits", "tokens in", "tokens out", "USD"):
        table.add_column(column, justify="right")
    for group in groups:
        table.add_row(
            "(none)" if group.key is None else group.key,
            str(group.calls),
            str(group.paid),
            str(group.hits),
            f"{group.input_tokens:,}",
            f"{group.output_tokens:,}",
            f"{group.cost_usd:.4f}",
        )
    return table


@llm_app.command("usage")
def usage_cmd(
    ctx: typer.Context,
    run: Annotated[str | None, typer.Option("--run", help="Only the calls of this run id.")] = None,
    day: Annotated[
        str | None, typer.Option("--day", help="Only the calls billed on this UTC day.")
    ] = None,
    by: Annotated[
        list[UsageBy] | None,
        typer.Option("--by", help="Group by day, run, agent or model (repeatable; default all)."),
    ] = None,
    store: StoreOption = None,
) -> None:
    """Show LLM spend per day, run, agent and model from the usage ledger."""
    console = Console(soft_wrap=False, highlight=False)
    try:
        on_day = None if day is None else date.fromisoformat(day.strip())
    except ValueError:
        raise typer.BadParameter(
            f"{day!r} is not a date like 2026-11-17", param_hint="--day"
        ) from None
    chosen = {choice.value for choice in by or ()}
    dimensions = tuple(d for d in USAGE_DIMENSIONS if not chosen or d in chosen)
    filters = [f"run {run}"] if run else []
    filters += [f"day {on_day}"] if on_day else []
    scope = f" ({', '.join(filters)})" if filters else ""
    with _open(
        ctx, store, console, read_only=True, what="LLM usage ledger", tables=(LLM_USAGE_TABLE,)
    ) as database:
        ledger = CostLedger(database)
        (total,) = ledger.usage_summary(run_id=run, day=on_day)
        tables = {d: ledger.usage_summary(d, run_id=run, day=on_day) for d in dimensions}
    if total.calls == 0:
        console.print(f"No LLM usage recorded{scope}.")
        return
    console.print(_usage_table(f"LLM usage{scope}", "", (replace(total, key="total"),)))
    for dimension in dimensions:
        console.print(_usage_table(f"per {dimension}", dimension, tables[dimension]))


@cache_app.command("stats")
def stats_cmd(ctx: typer.Context, store: StoreOption = None) -> None:
    """Show what the cache holds and the hit rate of the logged calls."""
    console = Console(soft_wrap=False, highlight=False)
    tables = (LLM_CACHE_TABLE, LLM_USAGE_TABLE)
    with _open(ctx, store, console, read_only=True, tables=tables) as database:
        stats = LLMCache(database).stats()
        hits, paid = CostLedger(database).cache_counts()
    table = Table(title="LLM cache", show_header=False)
    table.add_column(justify="left")
    table.add_column(justify="right")
    table.add_row("entries", str(stats.entries))
    table.add_row("size (bytes)", f"{stats.bytes:,}")
    for model, count in sorted(stats.models.items()):
        table.add_row(f"  {model}", str(count))
    table.add_row("oldest", str(stats.oldest) if stats.oldest else "n/a")
    table.add_row("newest", str(stats.newest) if stats.newest else "n/a")
    table.add_row("hits", str(hits))
    table.add_row("paid calls", str(paid))
    table.add_row("hit rate", f"{hits / (hits + paid):.1%}" if hits + paid else "n/a")
    console.print(table)


@cache_app.command("export")
def export_cmd(
    ctx: typer.Context,
    out: Annotated[Path, typer.Argument(help="File to write: one JSON object per line.")],
    store: StoreOption = None,
    force: Annotated[bool, typer.Option("--force", help="Replace OUT if it exists.")] = False,
) -> None:
    """Write every cache entry to OUT as JSON lines, oldest first."""
    console = Console(soft_wrap=False, highlight=False)
    if out.exists() and not force:
        console.print(f"[red]{out} exists:[/red] pass --force to replace it.")
        raise typer.Exit(1)
    with _open(ctx, store, console, read_only=True) as database:
        entries = LLMCache(database).entries()
    write_export(entries, out)
    console.print(f"Exported {len(entries)} entries to {out}.")


@cache_app.command("import")
def import_cmd(
    ctx: typer.Context,
    source: Annotated[
        Path,
        typer.Argument(
            metavar="FILE",
            exists=True,
            dir_okay=False,
            readable=True,
            help="An export file: one JSON object per line.",
        ),
    ],
    store: StoreOption = None,
    replace: Annotated[
        bool, typer.Option("--replace", help="Replace entries that are already stored.")
    ] = False,
) -> None:
    """Store the entries of an export FILE in the cache (creating the cache if needed)."""
    console = Console(soft_wrap=False, highlight=False)
    try:
        entries = read_export(source)
    except LLMError as exc:
        console.print(f"[red]Cannot import:[/red] {exc}")
        raise typer.Exit(1) from None
    with _open(ctx, store, console, read_only=False, create=True) as database:
        stored = LLMCache(database).load(entries, replace=replace)
    skipped = len(entries) - stored
    note = f" ({skipped} already stored; pass --replace to overwrite)" if skipped else ""
    console.print(f"Imported {stored} of {len(entries)} entries from {source}{note}.")


@cache_app.command("prune")
def prune_cmd(
    ctx: typer.Context,
    older_than: Annotated[
        str,
        typer.Option("--older-than", help="Age (30d, 12h) or UTC date (YYYY-MM-DD) to prune."),
    ],
    store: StoreOption = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Only count.")] = False,
) -> None:
    """Delete the cache entries recorded before the cutoff."""
    console = Console(soft_wrap=False, highlight=False)
    try:
        cutoff = parse_older_than(older_than, LiveClock().now())
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--older-than") from None
    with _open(ctx, store, console, read_only=dry_run) as database:
        cache = LLMCache(database)
        if dry_run:
            n = sum(1 for e in cache.entries() if e.created_at < cutoff)
            console.print(f"Dry run: would delete {n} entries recorded before {cutoff}.")
        else:
            n = cache.prune(cutoff)
            console.print(f"Pruned: deleted {n} entries recorded before {cutoff}.")


__all__ = ["cache_app", "llm_app", "parse_older_than"]
