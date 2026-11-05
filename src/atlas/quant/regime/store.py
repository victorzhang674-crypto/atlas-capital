"""The persisted daily regime history (Stage 21): lake table `regime_history`, one row per
decision day.

Columns: `date` (the decision day, key), `data_date` (the last session whose data went in), the
labels, `stress_flag`, `risk_scalar`, the HMM probabilities `hmm_p0..hmm_p2` (calm first; NaN
beyond `hmm_states`, which is 0 without an HMM read), `credit_source`, the readings of
`RegimeState`, `missing` (a string list), `config_hash` (a tag of the settings the values depend
on) and `available_at` = `bar_available_at(data_date)` — when the last input is published, so a
reader whose decision as-of is the morning of `date` sees the row and one at `data_date` (or
earlier) does not (`pit_filter`, `assert_no_lookahead`). Reads take a decision as-of (a `date`
uses the configured decision time) and only see rows of the current `config_hash`. Writing
replaces the rows of the same dates (the key is `date`, so a lake holds one history: writing
with other settings replaces those dates' rows). The store's lake may differ from the data
lake.
"""

import hashlib
import json
import math
from collections.abc import Sequence
from datetime import date, datetime

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig
from atlas.core.logging import get_logger
from atlas.core.pit import AsOf, assert_no_lookahead, bar_available_at, pit_filter
from atlas.core.storage import (
    AVAILABLE_AT,
    DEFAULT_REGISTRY,
    YEAR,
    Column,
    ColumnType,
    Lake,
    TableSchema,
)
from atlas.quant.regime.hmm import state_labels
from atlas.quant.regime.loader import regime_history
from atlas.quant.regime.state import RegimeState

_log = get_logger(__name__)

READINGS: tuple[str, ...] = (
    "realised_vol",
    "vix",
    "vol_score",
    "price_to_sma_200",
    "momentum_12_1",
    "breadth",
    "credit_z",
    "nfci",
    "growth_score",
    "inflation_gap",
)
"""Float readings of `RegimeState` stored as nullable columns."""

MAX_STATES = 3
"""HMM probability columns (`hmm_p0..hmm_p2`)."""

_F, _S = ColumnType.FLOAT64, ColumnType.STRING

REGIME_HISTORY = DEFAULT_REGISTRY.register(
    TableSchema(
        name="regime_history",
        columns=(
            Column(name="date", dtype=ColumnType.DATE),
            Column(name="data_date", dtype=ColumnType.DATE),
            Column(name="vol_regime", dtype=_S),
            Column(name="trend_regime", dtype=_S),
            Column(name="macro_quadrant", dtype=_S, nullable=True),
            Column(name="stress_flag", dtype=ColumnType.BOOL),
            Column(name="risk_scalar", dtype=_F),
            *(Column(name=f"hmm_p{k}", dtype=_F, nullable=True) for k in range(MAX_STATES)),
            Column(name="hmm_states", dtype=ColumnType.INT64),
            Column(name="credit_source", dtype=_S, nullable=True),
            *(Column(name=name, dtype=_F, nullable=True) for name in READINGS),
            Column(name="missing", dtype=ColumnType.STRING_LIST),
            Column(name="config_hash", dtype=_S),
            Column(name=AVAILABLE_AT, dtype=ColumnType.TIMESTAMP),
        ),
        primary_key=("date",),
        partition_by=(YEAR,),
        year_column="date",
        description="Daily market-regime states (RegimeState) by decision day; Stage 21",
    )
)
"""`regime_history` (module docstring)."""


def regime_config_hash(config: AtlasConfig) -> str:
    """A 12-hex-char tag of the settings the stored states depend on: `quant.regime`, the
    benchmark, `quant.features.sessions_per_year` and the macro timing / transform keys."""
    macro = config.data.macro
    payload = {
        "regime": config.quant.regime.model_dump(mode="json"),
        "benchmark": config.fund.benchmark,
        "sessions_per_year": config.quant.features.sessions_per_year,
        "macro": {
            "market_lag_days": macro.market_lag_days,
            "market_available_time": str(macro.market_available_time),
            "transform_lag_tolerance_days": macro.transform_lag_tolerance_days,
        },
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _float(value: object) -> float | None:
    """A stored nullable float as `float | None` (NaN, None or ±inf -> None)."""
    number = float("nan") if value is None else float(value)  # type: ignore[arg-type]
    return number if math.isfinite(number) else None


class RegimeStore:
    """Read and write `regime_history` in `lake` for the settings of `config` (default
    `AtlasConfig()`); only rows with the current `config_hash` are read."""

    def __init__(self, lake: Lake, config: AtlasConfig | None = None) -> None:
        self._lake = lake
        self._cfg = config if config is not None else AtlasConfig()
        self.config_hash = regime_config_hash(self._cfg)
        self._calendar = TradingCalendar(config=self._cfg.calendar)

    def _point(self, as_of: date | AsOf) -> AsOf:
        if isinstance(as_of, AsOf):
            return as_of
        if isinstance(as_of, datetime):
            raise TypeError("pass a trading day (date) or an AsOf, not a datetime")
        return AsOf.for_session(as_of, config=self._cfg.calendar)

    def _row(self, state: RegimeState) -> dict[str, object]:
        probs = list(state.hmm_probs or ())
        row: dict[str, object] = {
            "date": state.as_of,
            "data_date": state.data_date,
            "vol_regime": state.vol_regime,
            "trend_regime": state.trend_regime,
            "macro_quadrant": state.macro_quadrant,
            "stress_flag": state.stress_flag,
            "risk_scalar": state.risk_scalar,
            "hmm_states": len(probs),
            "credit_source": state.credit_source,
            "missing": list(state.missing),
            "config_hash": self.config_hash,
            AVAILABLE_AT: pd.Timestamp(bar_available_at(state.data_date, self._calendar)),
        }
        for k in range(MAX_STATES):
            row[f"hmm_p{k}"] = probs[k] if k < len(probs) else np.nan
        for name in READINGS:
            value = getattr(state, name)
            row[name] = np.nan if value is None else value
        return row

    def write(self, states: Sequence[RegimeState]) -> int:
        """Store `states` (replacing stored rows of the same dates); returns the rows written."""
        if not states:
            return 0
        frame = pd.DataFrame([self._row(s) for s in states])
        frame = frame.drop_duplicates("date", keep="last")
        frame[AVAILABLE_AT] = pd.to_datetime(frame[AVAILABLE_AT], utc=True).astype(
            "datetime64[us, UTC]"
        )
        frame = frame[list(REGIME_HISTORY.column_names)]
        if self._lake.has_table(REGIME_HISTORY.name):  # key `date`: an upsert replaces
            self._lake.write_table(REGIME_HISTORY.name, frame, mode="upsert")
        else:
            self._lake.write_table(REGIME_HISTORY.name, frame, mode="overwrite")
        _log.info(
            "regime.history_stored",
            rows=len(frame),
            first=str(frame["date"].min()),
            last=str(frame["date"].max()),
        )
        return len(frame)

    def _rows(self, filters: list[tuple[str, str, object]]) -> pd.DataFrame:
        if not self._lake.has_table(REGIME_HISTORY.name):
            return pd.DataFrame(columns=list(REGIME_HISTORY.column_names))
        rows = self._lake.read_table(REGIME_HISTORY.name, filters=filters)
        return rows[rows["config_hash"] == self.config_hash]

    @staticmethod
    def _state(row: pd.Series) -> RegimeState:
        n = int(row["hmm_states"])
        probs = tuple(float(row[f"hmm_p{k}"]) for k in range(n)) if n else None
        quadrant = row["macro_quadrant"]
        source = row["credit_source"]
        return RegimeState(
            as_of=row["date"],
            data_date=row["data_date"],
            vol_regime=row["vol_regime"],
            trend_regime=row["trend_regime"],
            hmm_probs=probs,
            hmm_labels=state_labels(n) if n else (),
            macro_quadrant=quadrant if isinstance(quadrant, str) else None,
            stress_flag=bool(row["stress_flag"]),
            risk_scalar=float(row["risk_scalar"]),
            credit_source=source if isinstance(source, str) else None,
            missing=tuple(row["missing"]),
            **{name: _float(row[name]) for name in READINGS},
        )

    def read(
        self, start: date, end: date, *, as_of: date | AsOf | None = None
    ) -> list[RegimeState]:
        """The stored states dated in [`start`, `end`], ascending; with `as_of`, only those
        known at that decision (point in time)."""
        if start > end:
            raise ValueError(f"start ({start}) must not be after end ({end})")
        rows = self._rows([("date", ">=", start), ("date", "<=", end)])
        if as_of is not None:
            point = self._point(as_of)
            rows = pit_filter(rows, point)
            assert_no_lookahead(rows, point)
        rows = rows.sort_values("date")
        return [self._state(row) for _, row in rows.iterrows()]

    def latest(self, as_of: date | AsOf) -> RegimeState | None:
        """The latest stored state dated on or before `as_of.day` that is known at `as_of`
        (a decision day sees its own state), or `None`."""
        point = self._point(as_of)
        rows = self._rows([("date", "<=", point.day)])
        if rows.empty:
            return None
        known = pit_filter(rows, point)
        assert_no_lookahead(known, point)
        if known.empty:
            return None
        return self._state(known.sort_values("date").iloc[-1])

    def dates(self) -> tuple[date, ...]:
        """The stored decision days with the current settings, ascending."""
        rows = self._rows([])
        return tuple(sorted(set(rows["date"])))


def build_and_store(
    lake: Lake,
    store: RegimeStore,
    start: date,
    end: date,
    config: AtlasConfig | None = None,
) -> list[RegimeState]:
    """`regime_history` of [`start`, `end`] from the data in `lake`, written to `store`;
    returns the states."""
    states = regime_history(lake, start, end, config)
    store.write(states)
    return states
