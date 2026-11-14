"""`atlas llm cache …`: the LLM record/replay cache (Stage 25).

- `atlas llm cache stats` shows what is stored (entries, size, entries per model, oldest and
  newest recording) and the hit rate of the calls in the usage ledger (`llm_usage`).
- `atlas llm cache export OUT.jsonl` writes every entry as one JSON line (oldest first): `key`,
  `created_at`, `provider`, `model`, `request`, `response` and `usage` (tokens and `cost_usd`
  of the recorded call). An existing file is only replaced with `--force`.
- `atlas llm cache prune --older-than 30d` deletes the entries recorded more than 30 days (or
  `12h` hours) ago, or before a `YYYY-MM-DD` date (UTC); `--dry-run` only counts them.

All three read the lake given by `--store` (default: the data lake, `data.lake_path`) and never
create one. Exit codes: 0 ok, 1 no cache in the store / configuration error / file exists,
2 bad arguments.
"""

import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from atlas.core.clock import LiveClock
from atlas.core.config import load_config
from atlas.core.errors import AtlasError, ConfigError
from atlas.core.storage import Database, Lake
from atlas.core.storage.db import DB_FILENAME
from atlas.llm.cache import LLMCache
from atlas.llm.cost import CostLedger

llm_app = typer.Typer(
    help="LLM tools: the record/replay cache.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
cache_app = typer.Typer(
    help="The LLM record/replay cache: stats, export, prune.",
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


def _open(ctx: typer.Context, store: Path | None, console: Console, *, read_only: bool) -> Database:
    """The database of `store` (or of the configured data lake); exit 1 when it has none."""
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
    if not (Path(root) / DB_FILENAME).is_file():
        console.print(f"[red]No LLM cache:[/red] {Path(root) / DB_FILENAME} does not exist.")
        raise typer.Exit(1)
    try:
        return Database(Lake(root), read_only=read_only)
    except AtlasError as exc:  # pragma: no cover - defensive: an unreadable database file
        console.print(f"[red]Cannot open the cache:[/red] {exc}")
        raise typer.Exit(1) from None


@cache_app.command("stats")
def stats_cmd(ctx: typer.Context, store: StoreOption = None) -> None:
    """Show what the cache holds and the hit rate of the logged calls."""
    console = Console(soft_wrap=False, highlight=False)
    with _open(ctx, store, console, read_only=True) as database:
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
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for entry in entries:
            line = {
                "key": entry.key,
                "created_at": entry.created_at.astimezone(UTC).isoformat(),
                "provider": entry.provider,
                "model": entry.model,
                "request": json.loads(entry.request_json),
                "response": json.loads(entry.response_json),
                "usage": json.loads(entry.usage_json),
            }
            handle.write(json.dumps(line, sort_keys=True, ensure_ascii=False) + "\n")
    console.print(f"Exported {len(entries)} entries to {out}.")


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
