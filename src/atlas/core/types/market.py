"""Market data contracts."""

import datetime as dt
from typing import Annotated, Self

from pydantic import Field, model_validator

from atlas.core.types.base import AtlasModel
from atlas.core.types.instruments import Symbol

_Price = Annotated[float, Field(gt=0)]


class Bar(AtlasModel):
    """One daily OHLCV bar for `symbol` on trading day `date`.

    `open`/`high`/`low`/`close` are raw (unadjusted) prices per share in the instrument's
    currency; `adj_close` is the close adjusted for splits and dividends; `volume` is in
    shares. Requires `high >= max(open, close, low)` and `low <= min(open, close)`.
    """

    symbol: Symbol
    date: dt.date
    open: _Price
    high: _Price
    low: _Price
    close: _Price
    adj_close: _Price
    volume: Annotated[float, Field(ge=0)]

    @model_validator(mode="after")
    def _ohlc_consistent(self) -> Self:
        if self.high < max(self.open, self.close, self.low):
            raise ValueError(f"high {self.high} is below open, close or low")
        if self.low > min(self.open, self.close):
            raise ValueError(f"low {self.low} is above open or close")
        return self
