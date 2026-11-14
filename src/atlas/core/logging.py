"""Structured logging: structlog on top of stdlib `logging`.

Every record, from Atlas loggers and from third-party stdlib loggers alike, passes through
one processor chain that adds the current run's `run_id`, `mode`, `as_of` and `stage`, the
level, the logger name and an ISO-8601 UTC timestamp. The file handler writes one JSON object
per line; the console handler writes human-readable text.
"""

import logging
import sys
from pathlib import Path
from typing import TextIO

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from atlas.core.run_context import current_run_context, process_run_context

_installed: list[logging.Handler] = []


def add_run_context(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """structlog processor: copy the current run's `run_id` and `mode` into the record, plus
    `as_of` (ISO date) and `stage` when set. A record from a thread without a run context of
    its own (a helper thread a library started) gets the process's only open run
    (`process_run_context`). Does nothing outside a run.

    The run's values win, so every line is tagged with its run. A caller's own value under one
    of these keys that differs is kept in `shadowed` (`{"mode": "upsert"}`), not dropped.
    """
    ctx = current_run_context()
    if ctx is None:
        ctx = process_run_context()
    if ctx is None:
        return event_dict
    run_values = {"run_id": ctx.run_id, "mode": ctx.mode}
    if ctx.as_of is not None:
        run_values["as_of"] = ctx.as_of.isoformat()
    if ctx.stage is not None:
        run_values["stage"] = ctx.stage
    shadowed = {
        key: event_dict[key]
        for key, value in run_values.items()
        if key in event_dict and event_dict[key] != value
    }
    event_dict.update(run_values)
    if shadowed:
        event_dict["shadowed"] = shadowed
    return event_dict


def _shared_processors() -> list[Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        add_run_context,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]


def _configure_structlog() -> None:
    structlog.configure(
        processors=[*_shared_processors(), structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


def _formatter(renderer: Processor) -> structlog.stdlib.ProcessorFormatter:
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_shared_processors(),
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )


def configure_logging(
    *,
    level: str = "INFO",
    log_file: Path | None = None,
    console: TextIO | None = None,
    console_level: str | None = None,
) -> None:
    """Send all logging to the console (pretty text) and, if `log_file` is given, to that
    file as JSON lines (parent directories are created).

    `level` is a stdlib level name applied to the root logger; `console_level` (default: no
    extra filter) raises the console's threshold only, so a CLI can keep the console quiet
    while the file keeps every record. `console` defaults to `sys.stderr`. Calling it again
    closes and replaces the handlers installed last time.
    """
    reset_logging()
    _configure_structlog()
    stream = console if console is not None else sys.stderr
    console_handler = logging.StreamHandler(stream)
    console_handler.setFormatter(_formatter(structlog.dev.ConsoleRenderer(colors=stream.isatty())))
    if console_level is not None:
        console_handler.setLevel(console_level)
    handlers: list[logging.Handler] = [console_handler]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(_formatter(structlog.processors.JSONRenderer(sort_keys=True)))
        handlers.append(file_handler)
    root = logging.getLogger()
    root.setLevel(level)
    for handler in handlers:
        root.addHandler(handler)
        _installed.append(handler)


def reset_logging() -> None:
    """Remove and close the handlers that `configure_logging` installed."""
    root = logging.getLogger()
    while _installed:
        handler = _installed.pop()
        root.removeHandler(handler)
        handler.close()


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """A structlog logger for `name` (use the module's `__name__`). Records carry the current
    run's fields; they are written once `configure_logging` has installed handlers."""
    if not structlog.is_configured():
        _configure_structlog()
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger
