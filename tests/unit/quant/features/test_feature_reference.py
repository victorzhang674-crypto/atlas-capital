"""Stage 15 Task 6: the reference implementation and its fixture CSVs."""

import math

import feature_reference as ref
import pytest


def _same(a: float, b: float, rel: float) -> bool:
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    return math.isclose(a, b, rel_tol=rel, abs_tol=1e-12)


def test_expected_csv_is_current() -> None:
    stored = ref.read_expected()
    fresh = ref.expected_rows(ref.read_ohlcv())
    assert [(d, s) for d, s, _ in stored] == [(d, s) for d, s, _ in fresh]
    for (day, symbol, got), (_, _, want) in zip(stored, fresh, strict=True):
        for name, a, b in zip(ref.FEATURE_NAMES, got, want, strict=True):
            assert _same(a, b, 1e-10), (day, symbol, name, a, b)


def test_ohlcv_fixture_has_the_designed_events() -> None:
    rows = ref.read_ohlcv()
    days = sorted({r["date"] for r in rows})  # type: ignore[type-var]
    assert len(days) == ref.N_SESSIONS
    by = {(r["symbol"], r["date"]): r for r in rows}
    aaa = [by["AAA", d] for d in days]
    raw = aaa[ref.SPLIT_AT]["close"] / aaa[ref.SPLIT_AT - 1]["close"]  # type: ignore[operator]
    assert 0.45 < raw < 0.55  # the 2:1 split
    assert aaa[0]["adj_close"] != aaa[0]["close"]  # adjusted for the split and the dividend
    assert aaa[-1]["adj_close"] == aaa[-1]["close"]  # relative to the last bar
    assert ("BBB", days[ref.MISSING_AT]) not in by
    assert sum(1 for r in rows if r["symbol"] == "BBB") == ref.N_SESSIONS - 1
    assert len(ref.FEATURE_NAMES) == 40


def test_reference_building_blocks_hand_values() -> None:
    nan = math.nan
    assert ref.ema([1, 2, 3, 4, 5], 3) == pytest.approx([nan, nan, 2.0, 3.0, 4.0], nan_ok=True)
    # Wilder n=2 (alpha 1/2): seed mean(2, 4) = 3, then 3 + (6 - 3)/2 = 4.5; a gap keeps state
    assert ref.wilder([2, 4, 6, nan, 8], 2) == pytest.approx(
        [nan, 3.0, 4.5, nan, 6.25], nan_ok=True
    )
    assert ref.std([1.0, 2.0, 3.0], 0) == pytest.approx(math.sqrt(2 / 3))
    assert ref.lagged_ratio([100.0, 110.0, 99.0], 0, 1) == pytest.approx(
        [nan, 0.1, -0.1], nan_ok=True
    )


def test_reference_features_hand_values() -> None:
    n = 300
    up = [100.0 * 1.01**t for t in range(n)]
    s = ref.Series(o=up, h=[v * 1.01 for v in up], lo=[v * 0.99 for v in up], c=up, dv=[1e6] * n)
    f = ref.features(s, s)
    assert f["rsi_14"][-1] == 100.0  # gains only
    assert f["ret_1d"][-1] == pytest.approx(0.01)
    assert f["max_dd_252d"][-1] == 0.0
    assert f["vol_21d"][-1] == pytest.approx(0.0, abs=1e-12)
    assert math.isnan(f["beta_252d"][-1])  # a benchmark with constant returns: no beta
    zigzag = [100.0 * (1.02 if t % 2 else 0.99) ** (t % 7) for t in range(n)]
    s3 = ref.Series(o=zigzag, h=zigzag, lo=zigzag, c=zigzag, dv=[1e6] * n)
    f3 = ref.features(s3, s3)
    assert f3["beta_252d"][-1] == pytest.approx(1.0)
    assert f3["idio_vol_252d"][-1] == pytest.approx(0.0, abs=1e-9)
    path = [100.0, 120.0, 90.0, 130.0, 117.0] * 60
    s2 = ref.Series(o=path, h=path, lo=path, c=path, dv=[1e6] * 300)
    assert ref.features(s2, s2)["max_dd_252d"][-1] == pytest.approx(-0.3076923076923077)
    # within any 252-session window the worst fall is 130 -> 90: 90/130 - 1
