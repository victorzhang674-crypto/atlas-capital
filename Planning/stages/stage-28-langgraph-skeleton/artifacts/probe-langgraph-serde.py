import sys, warnings
sys.path.insert(0, sys.argv[1])
warnings.simplefilter("error")
from datetime import date
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from atlas.core.types.research import AnalystReport, Signal, Direction, MacroOutlook, AssetView
r = AnalystReport(ticker="AAA", as_of=date(2024,1,2), analyst="fundamental", summary="s", signals=(Signal(name="x", direction=Direction.LONG if hasattr(Direction,"LONG") else list(Direction)[0], strength=0.5, evidence_ids=("e1",)),), risks=("r",), confidence=0.4)
m = MacroOutlook(as_of=date(2024,1,2), regime_summary="a", growth="g", inflation="i", policy="p", credit="c", risks=(), asset_views=(), sector_tilts={"Tech": 0.2}, risk_appetite=0.5, evidence=())
allowed = [("atlas.core.types.research","AnalystReport"),("atlas.core.types.research","Signal"),("atlas.core.types.research","Direction"),("atlas.core.types.research","MacroOutlook")]
s = JsonPlusSerializer(allowed_msgpack_modules=allowed)
val = {"q": ("AAA","BBB"), "r": {"AAA": {"f": r}}, "m": m, "d": date(2024,1,2), "e": (r,)}
t = s.dumps_typed(val)
back = s.loads_typed(t)
print(back["q"], type(back["q"]), back["r"]["AAA"]["f"] == r, back["m"] == m, type(back["m"].sector_tilts), back["e"])
s2 = JsonPlusSerializer(allowed_msgpack_modules=[("atlas.core.types.research","Signal")])
try:
    print("blocked ->", s2.loads_typed(t)["m"])
except Exception as e:
    print("blocked raise", type(e).__name__, e)
