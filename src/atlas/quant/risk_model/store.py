"""Persistence of the factor risk model in the lake (Stage 19).

- `factor_returns`: one row per (`date`, `factor`): the daily factor return (`value`, NaN when
  the factor was absent that day) with that day's regression `r2` and `n_names`.
- `risk_model_snapshots`: a `RiskModel` on a rebalance date as long rows (`date`, `component`,
  `row`, `column`, `value`): `exposure` (symbol x factor), `factor_cov` (factor x factor),
  `specific_var` (symbol, column ""), plus `dropped` (symbol, reason in `column`),
  `adjustment` (step in `row`), `etf` (symbol) and `meta` (`n_days`).

Both are keyed by the **data date**: the last session whose bars went into the row (a factor
return of session D, or a model whose newest factor return is D's). Both carry `config_hash` (a
tag of the risk-model settings and of the factor library that produced the exposures) and
`available_at` = when D's daily bar is published (`bar_available_at`), so a reader whose
decision as-of is the morning of D (or earlier) never sees them (`pit_filter`,
`assert_no_lookahead`); from the next session's decision on they are visible. Reads take a
decision as-of (a `date` uses the configured decision time). Writing replaces the rows of the
same key.
"""

import hashlib
import json
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
from atlas.quant.factors.fundamental import factor_config_hash
from atlas.quant.risk_model.factor_model import FactorHistory, FactorSet, RiskModel

_log = get_logger(__name__)

FACTOR_RETURNS = DEFAULT_REGISTRY.register(
    TableSchema(
        name="factor_returns",
        columns=(
            Column(name="date", dtype=ColumnType.DATE),
            Column(name="factor", dtype=ColumnType.STRING),
            Column(name="value", dtype=ColumnType.FLOAT64, nullable=True),
            Column(name="r2", dtype=ColumnType.FLOAT64, nullable=True),
            Column(name="n_names", dtype=ColumnType.INT64),
            Column(name="config_hash", dtype=ColumnType.STRING),
            Column(name=AVAILABLE_AT, dtype=ColumnType.TIMESTAMP),
        ),
        primary_key=("date", "factor"),
        partition_by=(YEAR,),
        year_column="date",
        description="Daily factor returns of the factor risk model (constrained WLS); Stage 19",
    )
)
"""`factor_returns` (module docstring)."""

RISK_MODEL_SNAPSHOTS = DEFAULT_REGISTRY.register(
    TableSchema(
        name="risk_model_snapshots",
        columns=(
            Column(name="date", dtype=ColumnType.DATE),
            Column(name="component", dtype=ColumnType.STRING),
            Column(name="row", dtype=ColumnType.STRING),
            Column(name="column", dtype=ColumnType.STRING),
            Column(name="value", dtype=ColumnType.FLOAT64, nullable=True),
            Column(name="config_hash", dtype=ColumnType.STRING),
            Column(name=AVAILABLE_AT, dtype=ColumnType.TIMESTAMP),
        ),
        primary_key=("date", "component", "row", "column"),
        partition_by=(YEAR,),
        year_column="date",
        description="Factor risk model snapshots on rebalance dates (long format); Stage 19",
    )
)
"""`risk_model_snapshots` (module docstring)."""


def risk_model_config_hash(config: AtlasConfig) -> str:
    """A 12-hex-char tag of the settings the stored values depend on: `quant.risk_model`
    (factor model, history and condition limits) and the factor library
    (`factor_config_hash`)."""
    rm = config.quant.risk_model
    payload = {
        "factor_model": rm.factor_model.model_dump(mode="json"),
        "min_history": rm.min_history,
        "max_condition": rm.max_condition,
        "factors": factor_config_hash(config),
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _point(as_of: date | AsOf, config: AtlasConfig) -> AsOf:
    if isinstance(as_of, AsOf):
        return as_of
    if isinstance(as_of, datetime):
        raise TypeError("pass a trading day (date) or an AsOf, not a datetime")
    return AsOf.for_session(as_of, config=config.calendar)


class RiskModelStore:
    """Read and write the risk model's tables in `lake` for the settings of `config`
    (default `AtlasConfig()`); only rows with the current `config_hash` are read."""

    def __init__(self, lake: Lake, config: AtlasConfig | None = None) -> None:
        self._lake = lake
        self._cfg = config if config is not None else AtlasConfig()
        self.config_hash = risk_model_config_hash(self._cfg)
        self._factors = FactorSet.from_config(self._cfg.quant.risk_model.factor_model)
        self._calendar = TradingCalendar(config=self._cfg.calendar)

    @property
    def lake(self) -> Lake:
        """The lake this store reads and writes."""
        return self._lake

    # --- helpers

    def _available(self, day: date) -> pd.Timestamp:
        """When the bar of session `day` is published: the earliest decision that may use a
        value computed from it."""
        return pd.Timestamp(bar_available_at(day, self._calendar))

    def _rows(self, table: str, filters: list[tuple[str, str, object]]) -> pd.DataFrame:
        rows = self._lake.read_table(table, filters=filters)
        return rows[rows["config_hash"] == self.config_hash]

    def _put(self, table: str, frame: pd.DataFrame, delete: list[tuple[str, str, object]]) -> None:
        frame = frame.copy()
        frame[AVAILABLE_AT] = frame[AVAILABLE_AT].astype("datetime64[us, UTC]")
        if self._lake.has_table(table):
            self._lake.delete_rows(table, delete)
            if not frame.empty:
                self._lake.write_table(table, frame, mode="upsert")
        else:
            self._lake.write_table(table, frame, mode="overwrite")

    # --- factor returns

    def write_factor_returns(self, history: FactorHistory) -> int:
        """Store every session of `history` (replacing stored rows of the same dates);
        returns the rows written."""
        frames = []
        for stamp in history.factor_returns.index:
            row = history.factor_returns.loc[stamp]
            day = stamp.date()
            frames.append(
                pd.DataFrame(
                    {
                        "date": day,
                        "factor": [str(f) for f in row.index],
                        "value": row.to_numpy(dtype="float64"),
                        "r2": float(history.r2[stamp]),
                        "n_names": int(history.n_names[stamp]),
                        "config_hash": self.config_hash,
                        AVAILABLE_AT: self._available(day),
                    }
                )
            )
        if not frames:
            return 0
        frame = pd.concat(frames, ignore_index=True)
        first, last = (
            history.factor_returns.index[0].date(),
            history.factor_returns.index[-1].date(),
        )
        self._put(
            FACTOR_RETURNS.name,
            frame[list(FACTOR_RETURNS.column_names)],
            [("date", ">=", first), ("date", "<=", last)],
        )
        _log.info(
            "risk_model.factor_returns_stored", rows=len(frame), first=str(first), last=str(last)
        )
        return len(frame)

    def _known_returns(self, as_of: date | AsOf) -> pd.DataFrame:
        point = _point(as_of, self._cfg)
        if not self._lake.has_table(FACTOR_RETURNS.name):
            return pd.DataFrame(columns=list(FACTOR_RETURNS.column_names))
        known = pit_filter(self._rows(FACTOR_RETURNS.name, [("date", "<=", point.day)]), point)
        assert_no_lookahead(known, point)
        return known

    def read_factor_returns(self, as_of: date | AsOf) -> pd.DataFrame | None:
        """The stored daily factor returns known at `as_of`, dates ascending x factors (the
        model's factor order), or `None` when none are stored."""
        known = self._known_returns(as_of)
        if known.empty:
            return None
        wide = known.pivot(index="date", columns="factor", values="value")
        wide = wide.reindex(columns=list(self._factors.names)).sort_index().astype("float64")
        wide.index = pd.DatetimeIndex(pd.to_datetime(wide.index), name="date")
        wide.columns.name = None
        return wide

    def read_diagnostics(self, as_of: date | AsOf) -> pd.DataFrame | None:
        """`r2` and `n_names` per stored session known at `as_of`, or `None`."""
        known = self._known_returns(as_of)
        if known.empty:
            return None
        diag = known.groupby("date")[["r2", "n_names"]].first().sort_index()
        diag.index = pd.DatetimeIndex(pd.to_datetime(diag.index), name="date")
        return diag

    # --- snapshots

    def write_snapshot(self, model: RiskModel) -> int:
        """Store `model` under its `as_of` day, replacing that day's rows; returns the rows."""
        day = model.as_of
        parts: list[pd.DataFrame] = []

        def add(
            component: str, rows: list[str], cols: list[str], values: np.ndarray | None
        ) -> None:
            parts.append(
                pd.DataFrame(
                    {
                        "component": component,
                        "row": rows,
                        "column": cols,
                        "value": np.nan if values is None else values,
                    }
                )
            )

        n, k = len(model.symbols), len(model.factors)
        add(
            "exposure",
            [s for s in model.symbols for _ in model.factors],
            list(model.factors) * n,
            model.x.reshape(-1),
        )
        add(
            "factor_cov",
            [f for f in model.factors for _ in model.factors],
            list(model.factors) * k,
            model.f.reshape(-1),
        )
        add("specific_var", list(model.symbols), [""] * n, model.d)
        if model.dropped:
            add("dropped", list(model.dropped), list(model.dropped.values()), None)
        if model.adjustments:
            add("adjustment", list(model.adjustments), [""] * len(model.adjustments), None)
        if model.etfs:
            add("etf", list(model.etfs), [""] * len(model.etfs), None)
        add("meta", ["n_days"], [""], np.array([float(model.n_days)]))
        frame = pd.concat(parts, ignore_index=True).assign(
            date=day, config_hash=self.config_hash, **{AVAILABLE_AT: self._available(day)}
        )
        self._put(
            RISK_MODEL_SNAPSHOTS.name,
            frame[list(RISK_MODEL_SNAPSHOTS.column_names)],
            [("date", "=", day)],
        )
        _log.info("risk_model.snapshot_stored", day=day.isoformat(), rows=len(frame))
        return len(frame)

    def snapshot_dates(self) -> tuple[date, ...]:
        """The stored snapshot dates with the current settings, ascending."""
        if not self._lake.has_table(RISK_MODEL_SNAPSHOTS.name):
            return ()
        rows = self._rows(RISK_MODEL_SNAPSHOTS.name, [])
        return tuple(sorted(set(rows["date"])))

    def read_snapshot(self, as_of: date | AsOf) -> RiskModel | None:
        """The latest stored model dated on or before `as_of.day` whose rows are known at
        `as_of`, or `None`. Symbols come back sorted."""
        point = _point(as_of, self._cfg)
        if not self._lake.has_table(RISK_MODEL_SNAPSHOTS.name):
            return None
        rows = self._rows(RISK_MODEL_SNAPSHOTS.name, [("date", "<=", point.day)])
        known = pit_filter(rows, point)
        assert_no_lookahead(known, point)
        if known.empty:
            return None
        day = max(known["date"])
        own = known[known["date"] == day]

        def part(component: str) -> pd.DataFrame:
            return own[own["component"] == component]

        exposure = part("exposure").pivot(index="row", columns="column", values="value")
        exposure = exposure.reindex(columns=list(self._factors.names)).sort_index()
        factor_cov = part("factor_cov").pivot(index="row", columns="column", values="value")
        factor_cov = factor_cov.reindex(index=exposure.columns, columns=exposure.columns)
        spec = part("specific_var").set_index("row")["value"].reindex(exposure.index)
        meta = part("meta").set_index("row")["value"]
        dropped = dict(zip(part("dropped")["row"], part("dropped")["column"], strict=True))
        return RiskModel(
            as_of=day,
            symbols=tuple(map(str, exposure.index)),
            factors=tuple(map(str, exposure.columns)),
            x=exposure.to_numpy(dtype="float64"),
            f=factor_cov.to_numpy(dtype="float64"),
            d=spec.to_numpy(dtype="float64"),
            dropped=dropped,
            adjustments=tuple(sorted(part("adjustment")["row"])),
            n_days=int(meta.get("n_days", 0)),
            etfs=tuple(sorted(part("etf")["row"])),
        )
