"""Post-earnings-announcement drift (PEAD) from Stage 12 earnings reactions (Stage 22).

For each member of `quant.alpha.pead.universe`, the latest earnings reaction known at the
view's as-of (`DataView.earnings_reactions(symbol, 1)`; known from the decision after its
reaction window closes) gives the direction of the drift: the sign of `car` (the window's
abnormal return against the benchmark, Stage 12). With `k` = the sessions after the window's
last session up to the session before the decision (0 at the first decision that knows it):

- `decay = max(0, 1 - k / decay_sessions)` (linear; no prediction from `k >= decay_sessions`);
- `score = sign(car) * decay` in [-1, 1];
- `exp_return_ann = drift_ann * score`: the annualised abnormal drift, fading with the decay;
- `confidence = decay * min(|car| / car_full, 1)`.

A name without a known reaction has no prediction.
"""

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AlphaPEADConfig
from atlas.data.view import DataView
from atlas.quant.alpha.base import alpha_frame, empty_alpha_frame


def pead_decay(age: int, decay_sessions: int) -> float:
    """`max(0, 1 - age / decay_sessions)` for a reaction `age` sessions old (`age >= 0`)."""
    if age < 0 or decay_sessions < 1:
        raise ValueError(f"need age >= 0 and decay_sessions >= 1, got {age}, {decay_sessions}")
    return max(0.0, 1.0 - age / decay_sessions)


def pead_signal(
    latest: pd.DataFrame, *, decay_sessions: int, drift_ann: float, car_full: float
) -> pd.DataFrame:
    """The alpha frame of `latest` (index symbol; `car` = the latest reaction's abnormal
    return, fraction; `age` = sessions since its window, module docstring); names whose decay
    is 0 are left out."""
    if latest.empty:
        return empty_alpha_frame()
    car = latest["car"].astype("float64")
    decay = latest["age"].map(lambda k: pead_decay(int(k), decay_sessions)).astype("float64")
    live = decay > 0
    car, decay = car[live], decay[live]
    score = car.map(np.sign).astype("float64") * decay
    strength = (car.abs() / car_full).clip(upper=1.0)
    return alpha_frame(score, drift_ann * score, decay * strength)


def _age(window_end: date, day: date, calendar: TradingCalendar) -> int:
    """Sessions strictly between `window_end` and the decision day `day`."""
    return max(len(calendar.sessions(window_end, day)) - 2, 0)


@dataclass(frozen=True)
class PEADModel:
    """The post-earnings-drift `AlphaModel` (module docstring); `config` overrides the view's
    `quant.alpha.pead`."""

    config: AlphaPEADConfig | None = None

    @property
    def name(self) -> str:
        """`pead`."""
        return "pead"

    @property
    def horizon(self) -> int:
        """The forecast horizon in sessions (`config.horizon`; default 21)."""
        return (self.config or AlphaPEADConfig()).horizon

    def predict(self, view: DataView) -> pd.DataFrame:
        """The alpha frame of the universe members with a live reaction at `view.as_of`."""
        cfg = self.config or view.config.quant.alpha.pead
        calendar = TradingCalendar(config=view.config.calendar)
        rows: dict[str, dict[str, float]] = {}
        for symbol in view.universe(cfg.universe):
            found = view.earnings_reactions(symbol, 1)
            if found.empty or pd.isna(found["car"].iloc[0]):
                continue
            end = pd.Timestamp(found["window_end"].iloc[0]).date()
            rows[symbol] = {
                "car": float(found["car"].iloc[0]),
                "age": float(_age(end, view.day, calendar)),
            }
        latest = pd.DataFrame.from_dict(rows, orient="index", columns=["car", "age"])
        return pead_signal(
            latest,
            decay_sessions=cfg.decay_sessions,
            drift_ann=cfg.drift_ann,
            car_full=cfg.car_full,
        )
