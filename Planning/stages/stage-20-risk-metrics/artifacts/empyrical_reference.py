"""Generate independent reference values for the Stage 20 metrics tests with empyrical.

Run in an isolated environment (empyrical is NOT a project dependency):

    uv run --no-project --python 3.12 --with empyrical-reloaded==0.7.2 \
        python -I empyrical_reference.py <out.json>

The return series are generated once from fixed seeds, rounded to 8 decimals and stored in
the JSON, so the tests read the exact same numbers that empyrical saw.
"""

import json
import sys

import empyrical as ep
import numpy as np
import pandas as pd
from scipy import stats


def series(seed: int, n: int, mu: float, vol: float) -> np.ndarray:
    rng = np.random.default_rng(seed)
    r = mu + vol * rng.standard_t(df=4, size=n) / np.sqrt(2.0)
    return np.round(r, 8)


def main(out: str) -> None:
    n = 504
    b = series(11, n, 0.0004, 0.011)
    noise = series(12, n, 0.0001, 0.006)
    r = np.round(0.0002 + 0.6 * b + noise, 8)
    idx = pd.bdate_range("2020-01-01", periods=n)
    rs, bs = pd.Series(r, index=idx), pd.Series(b, index=idx)
    rf_daily = 0.0001
    ref = {
        "annual_return": ep.annual_return(rs),
        "annual_volatility": ep.annual_volatility(rs),
        "cum_returns_final": ep.cum_returns_final(rs),
        "sharpe_ratio": ep.sharpe_ratio(rs),
        "sharpe_ratio_rf": ep.sharpe_ratio(rs, risk_free=rf_daily),
        "sortino_ratio": ep.sortino_ratio(rs),
        "sortino_ratio_req": ep.sortino_ratio(rs, required_return=rf_daily),
        "downside_risk": ep.downside_risk(rs),
        "calmar_ratio": ep.calmar_ratio(rs),
        "omega_ratio": ep.omega_ratio(rs),
        "omega_ratio_thr": ep.omega_ratio(rs, required_return=0.05),
        "max_drawdown": ep.max_drawdown(rs),
        "tail_ratio": ep.tail_ratio(rs),
        "skew": float(stats.skew(r)),
        "kurtosis": float(stats.kurtosis(r)),
        "beta": ep.beta(rs, bs),
        "alpha": ep.alpha(rs, bs),
        "alpha_rf": ep.alpha(rs, bs, risk_free=rf_daily),
        "beta_rf": ep.beta(rs, bs, risk_free=rf_daily),
        "up_capture": ep.up_capture(rs, bs),
        "down_capture": ep.down_capture(rs, bs),
        "up_down_capture": ep.up_down_capture(rs, bs),
        "excess_sharpe": ep.excess_sharpe(rs, bs),
        "value_at_risk_05": ep.value_at_risk(rs, cutoff=0.05),
        "value_at_risk_01": ep.value_at_risk(rs, cutoff=0.01),
        "conditional_value_at_risk_05": ep.conditional_value_at_risk(rs, cutoff=0.05),
        "rolling_sharpe_63_last": float(ep.roll_sharpe_ratio(rs, window=63).iloc[-1]),
        "rolling_volatility_63_last": float(ep.roll_annual_volatility(rs, window=63).iloc[-1]),
        "rolling_max_drawdown_63_last": float(ep.roll_max_drawdown(rs, window=63).iloc[-1]),
        "rolling_beta_63_last": float(ep.roll_beta(rs, bs, window=63).iloc[-1]),
    }
    payload = {
        "source": f"empyrical-reloaded {ep.__version__} (scipy {stats.__name__})",
        "periods": 252,
        "risk_free_daily": rf_daily,
        "dates": [d.date().isoformat() for d in idx],
        "returns": r.tolist(),
        "benchmark": b.tolist(),
        "reference": {k: float(v) for k, v in ref.items()},
    }
    with open(out, "w") as fh:
        json.dump(payload, fh, indent=1)
        fh.write("\n")
    for k, v in payload["reference"].items():
        print(f"{k:32s} {v: .10f}")


if __name__ == "__main__":
    main(sys.argv[1])
