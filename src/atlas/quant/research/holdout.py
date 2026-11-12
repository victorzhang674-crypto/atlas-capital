"""Does a research window read the hold-out? (Phase 2 checkpoint; design spec A7.)

`backtest.in_sample_end` (2021-12-31) is the last in-sample session: research that chooses or
tunes anything may read returns up to it and no further, so that the sessions after it stay an
honest out-of-sample test. A signal dated on decision day `t` is scored against returns up to
`h` sessions after `t` (`labels`), so a window reads the hold-out when the **label** of its
last date closes after the in-sample end, even if the date itself is in sample.

`holdout_check` only measures; the command that runs the research decides what to do
(`atlas research signal` refuses unless `--allow-holdout` is given, and records the override
in the audit log).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig


@dataclass(frozen=True)
class HoldoutCheck:
    """How far a research window reads. `in_sample_end`: `backtest.in_sample_end`;
    `last_decision`: the last evaluation date; `last_label_session`: the last session whose
    return the window reads (`last_decision` + the longest horizon, in sessions);
    `latest_decision_in_sample`: the last decision day whose longest label still closes on or
    before the in-sample end."""

    in_sample_end: date
    last_decision: date
    last_label_session: date
    latest_decision_in_sample: date

    @property
    def reads_holdout(self) -> bool:
        """Whether a return after the in-sample end is read."""
        return self.last_label_session > self.in_sample_end


def holdout_check(
    dates: Sequence[date | pd.Timestamp],
    horizons: Sequence[int] | None = None,
    *,
    config: AtlasConfig | None = None,
) -> HoldoutCheck:
    """The `HoldoutCheck` of evaluation `dates` (trading sessions; the latest one counts)
    scored at `horizons` sessions (default `quant.research.horizons`; the longest counts)
    against `config.backtest.in_sample_end` (default `AtlasConfig()`). An in-sample end on a
    closed day means the session before it. `ValueError` for no dates or a bad horizon."""
    cfg = config if config is not None else AtlasConfig()
    hs = tuple(cfg.quant.research.horizons if horizons is None else horizons)
    if not hs or any(isinstance(h, bool) or not isinstance(h, int) or h < 1 for h in hs):
        raise ValueError(f"horizons must be positive ints, got {horizons!r}")
    days = [pd.Timestamp(d).date() for d in dates]
    if not days:
        raise ValueError("dates must not be empty")
    calendar = TradingCalendar(config=cfg.calendar)
    end = cfg.backtest.in_sample_end
    last_in_sample = end if calendar.is_session(end) else calendar.prev_session(end)
    last = max(days)
    return HoldoutCheck(
        in_sample_end=end,
        last_decision=last,
        last_label_session=calendar.offset(last, max(hs)),
        latest_decision_in_sample=calendar.offset(last_in_sample, -max(hs)),
    )
