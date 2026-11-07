"""Stage 22 Task 1: the `AlphaModel` protocol and the output contract."""

import math

import numpy as np
import pandas as pd
import pytest

from atlas.data.view import DataView
from atlas.quant.alpha import (
    ALPHA_COLUMNS,
    AlphaModel,
    alpha_frame,
    check_alpha_frame,
    direction,
    empty_alpha_frame,
)


def _frame(**overrides: object) -> pd.DataFrame:
    data = {
        "score": [1.0, -0.5, 0.0],
        "exp_return_ann": [0.02, -0.01, 0.0],
        "confidence": [0.5, 1.0, 0.0],
    }
    data.update(overrides)  # type: ignore[arg-type]
    return pd.DataFrame(data, index=pd.Index(["AAA", "BBB", "CCC"], name="symbol"))


def test_contract_accepts_valid_frame() -> None:
    out = check_alpha_frame(_frame())
    assert tuple(out.columns) == ALPHA_COLUMNS
    assert out.index.name == "symbol"
    assert all(out.dtypes == "float64")


@pytest.mark.parametrize(
    "bad",
    [
        _frame(score=[1.0, math.nan, 0.0]),
        _frame(exp_return_ann=[0.02, math.inf, 0.0]),
        _frame(confidence=[1.5, 0.5, 0.5]),
        _frame(confidence=[-0.1, 0.5, 0.5]),
        _frame().drop(columns="confidence"),
        _frame().assign(extra=1.0),
        _frame().rename(index={"BBB": "AAA"}),
        _frame().rename_axis("ticker"),
        _frame().iloc[::-1],  # not sorted by symbol
    ],
)
def test_contract_rejects(bad: pd.DataFrame) -> None:
    with pytest.raises(ValueError):
        check_alpha_frame(bad)


def test_alpha_frame_aligns_drops_incomplete_and_sorts() -> None:
    score = pd.Series({"ZZZ": 1.0, "AAA": 2.0, "MMM": 3.0})
    exp = pd.Series({"ZZZ": 0.01, "AAA": np.nan, "MMM": 0.03})
    conf = pd.Series({"ZZZ": 0.4, "AAA": 0.4, "MMM": 0.4, "QQQ": 0.4})
    out = alpha_frame(score, exp, conf)
    assert list(out.index) == ["MMM", "ZZZ"]
    assert out.loc["MMM", "exp_return_ann"] == 0.03
    assert check_alpha_frame(out) is not None


def test_empty_frame_meets_contract() -> None:
    out = empty_alpha_frame()
    assert out.empty
    check_alpha_frame(out)


def test_direction() -> None:
    d = direction(_frame())
    assert d.to_dict() == {"AAA": 1, "BBB": -1, "CCC": 0}
    assert d.dtype == "int64"


def test_protocol_is_structural() -> None:
    class Dummy:
        @property
        def name(self) -> str:
            return "dummy"

        @property
        def horizon(self) -> int:
            return 21

        def predict(self, view: DataView) -> pd.DataFrame:
            return empty_alpha_frame()

    assert isinstance(Dummy(), AlphaModel)
    assert not isinstance(object(), AlphaModel)
