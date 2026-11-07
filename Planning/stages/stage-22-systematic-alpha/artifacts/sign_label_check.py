"""Stage 22 audit before accepting the re-specification: factor signs and label alignment on
the real lake (read only), at one in-sample decision day, from run 1's cached panels.

    ATLAS__DATA__LAKE_PATH=~/Projects/atlas-capital/data UV_NO_SYNC=1 uv run python \
        Planning/stages/stage-22-systematic-alpha/artifacts/sign_label_check.py <lake> <signals.pkl> [YYYY-MM-DD]

Checks: (1) each feature component is the signed raw Stage 15 feature (Spearman vs the raw
feature: momentum +, low-vol -, reversal -); (2) the decision view at t holds bars through
t-1 only, and `ret_21d` at t is close(t-1)/close(t-22)-1 from raw prices; (3) the 21-session
label of t equals close(t+21)/close(t)-1 from raw prices; (4) the composite IC of t recomputed
here equals the evaluation's IC history row.
"""

import pickle
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.quant.alpha import ComponentPanel, MultiFactorModel
from atlas.quant.features import compute_features
from atlas.quant.research import forward_returns

LAKE = Path(sys.argv[1]).expanduser()
DAY = date.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else date(2018, 6, 1)
H = 21
cfg = AtlasConfig()
cal = TradingCalendar(config=cfg.calendar)
lake = Lake(LAKE)
with Path(sys.argv[2]).expanduser().open("rb") as fh:
    sig = pickle.load(fh)  # noqa: S301 - our own file
assert DAY in sig["components"], DAY
comps: pd.DataFrame = sig["components"][DAY]
members = list(comps.index)
view = DataView(lake, DAY, cfg)
ok = True


def closes(frame: pd.DataFrame) -> pd.DataFrame:
    """`prices` bars (long) as adjusted closes, dates x symbols."""
    wide = frame.pivot(index="date", columns="symbol", values="adj_close")
    wide.index = pd.to_datetime(wide.index)
    return wide


# (1) signs
feats = compute_features(view, members, ["momentum_12_1", "vol_63d", "ret_21d"], history=1)
raw = {n: feats[n].iloc[-1].reindex(members) for n in ("momentum_12_1", "vol_63d", "ret_21d")}
for comp, src, sign in (("momentum", "momentum_12_1", 1), ("low_vol", "vol_63d", -1),
                        ("reversal", "ret_21d", -1)):
    rho = comps[comp].corr(raw[src], method="spearman")
    good = np.sign(rho) == sign and abs(rho) > 0.8
    ok &= bool(good)
    print(f"(1) {comp:9s} vs raw {src:14s}: spearman {rho:+.3f} (expected sign {sign:+d}) "
          f"{'OK' if good else 'FAIL'}")

# (2) decision view timing and ret_21d from raw prices
px = closes(view.prices(members, 30))
last_bar = pd.Timestamp(px.index[-1]).date()
good = last_bar == cal.prev_session(DAY)
ok &= good
print(f"(2) decision view at {DAY}: last bar {last_bar} (t-1 = {cal.prev_session(DAY)}) "
      f"{'OK' if good else 'FAIL'}")
manual = px.iloc[-1] / px.iloc[-22] - 1
diff = (manual - raw["ret_21d"]).abs().dropna()
good = len(diff) > 300 and float(diff.max()) < 1e-9
ok &= good
print(f"(2) ret_21d == close(t-1)/close(t-22)-1 on {len(diff)} names: max |diff| "
      f"{diff.max():.2e} {'OK' if good else 'FAIL'}")

# (3) label from raw prices
end = cal.offset(DAY, H)
labels = forward_returns(lambda d: DataView(lake, d, cfg), [DAY], members, (H,), config=cfg)
y = labels.horizon(H).iloc[0]
later = closes(DataView(lake, cal.next_session(end), cfg).prices(members, H + 5))
manual = later.loc[pd.Timestamp(end)] / later.loc[pd.Timestamp(DAY)] - 1
diff = (manual - y).abs().dropna()
good = len(diff) > 300 and float(diff.max()) < 1e-9
ok &= good
print(f"(3) label h{H} of {DAY} == close({end})/close({DAY})-1 on {len(diff)} names: "
      f"max |diff| {diff.max():.2e} {'OK' if good else 'FAIL'}")
assert end <= cfg.backtest.in_sample_end

# (4) composite IC of the day under the current config
panel = ComponentPanel(sig["components"][DAY], sig["sectors"][DAY], sig["resid_vol"][DAY])
z = MultiFactorModel().predict_components(view, panel)["score"]
ic = z.corr(y.reindex(z.index), method="spearman")
print(f"(4) composite (current weights) Spearman IC h{H} at {DAY}: {ic:+.4f} on {len(z)} names")
print("ALL CHECKS OK" if ok else "SOME CHECK FAILED")
raise SystemExit(0 if ok else 1)
