"""The `AlphaModel` protocol and its output contract (Stage 22).

An alpha model turns a point-in-time `DataView` into one row per name it has a view on:

- index `symbol` (unique, sorted);
- `score` (float): higher = more attractive; units are the model's own (documented there);
- `exp_return_ann` (float): the expected **annualised simple return, as a fraction**
  (0.03 = 3% a year), over the model's `horizon` and then annualised; the residual (sector /
  market-relative) return for cross-sectional models, the asset's own return for time-series
  ones;
- `confidence` (float in [0, 1]): how much the model trusts the row.

A name the model cannot score is **absent**: the frame never holds a NaN or infinite value and
never a guessed one. Timing: a view at decision day `t` (08:00) holds bars through `t - 1`, so
the prediction is acted on from the close of `t` (Stage 17 convention).
"""

from typing import Final, Protocol, runtime_checkable

import numpy as np
import pandas as pd

from atlas.core.errors import ConfigError
from atlas.data.view import DataView

ALPHA_COLUMNS: Final = ("score", "exp_return_ann", "confidence")
"""Columns of every alpha frame, in order."""


@runtime_checkable
class AlphaModel(Protocol):
    """A deterministic alpha model: `predict(view)` returns the alpha frame of the module
    docstring for the decision `view.as_of`, reading data only through `view`."""

    @property
    def name(self) -> str:
        """Short identifier of the model (e.g. `multifactor`)."""
        ...

    @property
    def horizon(self) -> int:
        """Sessions the forecast is calibrated for (before annualisation)."""
        ...

    def predict(self, view: DataView) -> pd.DataFrame:
        """The alpha frame at `view.as_of`."""
        ...


def check_horizon(model: AlphaModel, configured: int) -> None:
    """`ConfigError` unless `model.horizon` equals `configured`, the horizon of the config
    `predict` is about to use. A model built without its own config reports the code default
    while `predict` follows the view's config; when the two differ, the horizon the model
    states would not be the one it forecasts for."""
    if model.horizon != configured:
        raise ConfigError(
            f"{type(model).__name__} reports a horizon of {model.horizon} sessions but the "
            f"view's quant.alpha.{model.name} horizon is {configured}: build the model with "
            f"config=view.config.quant.alpha.{model.name}"
        )


def empty_alpha_frame() -> pd.DataFrame:
    """An alpha frame with no rows."""
    return pd.DataFrame(
        {col: pd.Series(dtype="float64") for col in ALPHA_COLUMNS},
        index=pd.Index([], name="symbol", dtype="object"),
    )


def check_alpha_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """`frame` if it meets the contract (module docstring), as float64 columns in
    `ALPHA_COLUMNS` order; `ValueError` naming the first breach otherwise."""
    if tuple(frame.columns) != ALPHA_COLUMNS and set(frame.columns) != set(ALPHA_COLUMNS):
        raise ValueError(f"alpha frame columns must be {ALPHA_COLUMNS}, got {list(frame.columns)}")
    if frame.index.name != "symbol":
        raise ValueError(f"alpha frame index must be named 'symbol', got {frame.index.name!r}")
    if not frame.index.is_unique:
        dupes = sorted(set(frame.index[frame.index.duplicated()]))
        raise ValueError(f"alpha frame has repeated symbols {dupes[:5]}")
    if not frame.index.is_monotonic_increasing:
        raise ValueError("alpha frame symbols must be sorted ascending")
    out = frame[list(ALPHA_COLUMNS)].astype("float64")
    values = out.to_numpy()
    if values.size and not np.isfinite(values).all():
        raise ValueError("alpha frame values must be finite (absent names, not NaN rows)")
    conf = out["confidence"]
    if ((conf < 0) | (conf > 1)).any():
        raise ValueError("confidence must be in [0, 1]")
    return out


def alpha_frame(score: pd.Series, exp_return_ann: pd.Series, confidence: pd.Series) -> pd.DataFrame:
    """The alpha frame of the names that have all three values (finite), sorted by symbol;
    the three Series are aligned on their index (symbols)."""
    joined = pd.concat(
        {
            "score": score.astype("float64"),
            "exp_return_ann": exp_return_ann.astype("float64"),
            "confidence": confidence.astype("float64"),
        },
        axis=1,
    )
    joined = joined.replace([np.inf, -np.inf], np.nan).dropna(how="any").sort_index()
    joined.index = pd.Index(joined.index, name="symbol", dtype="object")
    return check_alpha_frame(joined)


def direction(frame: pd.DataFrame) -> pd.Series:
    """The direction of each row of an alpha frame: the sign of `exp_return_ann` as
    -1 / 0 / +1 (int64)."""
    checked = check_alpha_frame(frame)
    return pd.Series(
        np.sign(checked["exp_return_ann"].to_numpy()).astype("int64"),
        index=checked.index,
        name="direction",
    )
