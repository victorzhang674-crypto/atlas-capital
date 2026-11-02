"""Run context: who is running, in which mode, for which trading day.

`RunContext` lives in a `ContextVar`, so it follows the code path (and asyncio tasks) without
being passed around. A new `threading.Thread` starts with no run context; run its target via
`contextvars.copy_context().run(...)` to carry the current one across.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from ulid import ULID

from atlas.core.config import AtlasConfig, Profile, config_fingerprint
from atlas.core.errors import AtlasError

_CURRENT: ContextVar["RunContext | None"] = ContextVar("atlas_run_context", default=None)


class RunContext(BaseModel):
    """The identity of one run, or of a scope (fund-day stage, backtest day) inside it.

    `run_id` is a 26-char ULID; `mode` is the config profile (`dev`/`backtest`/`paper`);
    `as_of` is the trading day being decided (`None` outside a dated step);
    `config_fingerprint` is `config_fingerprint(cfg)` when the run was opened with a config;
    `stage` names the current step of the run (for example `"risk"`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    mode: Profile
    as_of: date | None = None
    config_fingerprint: str | None = None
    stage: str | None = None


def current_run_context() -> RunContext | None:
    """The innermost active run context, or `None` outside any run."""
    return _CURRENT.get()


@contextmanager
def run_context(
    *,
    mode: Profile | None = None,
    as_of: date | None = None,
    config: AtlasConfig | None = None,
    stage: str | None = None,
) -> Iterator[RunContext]:
    """Open a run, or a scope inside the current run, for the duration of the `with` block.

    At top level this starts a **new run** with a fresh random ULID, also under a seeded id
    source (`use_ids`): a re-run must not reuse, and append to, an earlier run's
    `runs/<run_id>/` files. `mode` defaults to `config.profile` when `config` is given, else
    `"dev"`; a `mode` that disagrees with `config.profile` raises `AtlasError`. Nested inside a
    run it opens a **child scope**: `run_id`, `mode` and `config_fingerprint` are inherited and
    only `as_of` and `stage` may change (arguments left as `None` keep the parent's value);
    passing `mode` or `config` raises `AtlasError`. The previous context is restored on exit,
    also on an exception.
    """
    parent = _CURRENT.get()
    if parent is None:
        if config is not None and mode is not None and mode != config.profile:
            raise AtlasError(
                f"run mode {mode!r} disagrees with the config profile {config.profile!r}"
            )
        ctx = RunContext(
            run_id=str(ULID()),
            mode=mode or (config.profile if config is not None else "dev"),
            as_of=as_of,
            config_fingerprint=config_fingerprint(config) if config is not None else None,
            stage=stage,
        )
    else:
        if mode is not None or config is not None:
            raise AtlasError(
                "a nested run context inherits run_id, mode and config; "
                "it may only change as_of and stage"
            )
        ctx = parent.model_copy(
            update={
                "as_of": as_of if as_of is not None else parent.as_of,
                "stage": stage if stage is not None else parent.stage,
            }
        )
    token = _CURRENT.set(ctx)
    try:
        yield ctx
    finally:
        _CURRENT.reset(token)


def run_dir(lake_path: Path, run_id: str) -> Path:
    """Directory holding one run's files: `<lake_path>/runs/<run_id>` (`lake_path` is
    `cfg.data.lake_path`, `data` by default; not resolved here)."""
    return lake_path / "runs" / run_id
