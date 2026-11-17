import operator, sqlite3, threading, time, contextvars, warnings
from datetime import date
from typing import Annotated, TypedDict
from pydantic import BaseModel
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from langgraph.checkpoint.sqlite import SqliteSaver
warnings.simplefilter("always")
CV = contextvars.ContextVar("cv", default=None)

class Rep(BaseModel):
    symbol: str; analyst: str; d: date

def merge(a, b):
    out = {k: dict(v) for k, v in (a or {}).items()}
    for k, v in (b or {}).items():
        out.setdefault(k, {}).update(v)
    return out
def merge1(a, b):
    out = dict(a or {}); out.update(b or {}); return out

class Fund(TypedDict, total=False):
    as_of: date
    queue: list[str]
    reports: Annotated[dict, merge]
    theses: Annotated[dict, merge1]
    errors: Annotated[list, operator.add]

class RIn(TypedDict):
    as_of: date; symbol: str
class R(TypedDict, total=False):
    as_of: date; symbol: str
    areports: Annotated[dict, merge1]
    thesis: object
    reports: Annotated[dict, merge]
    theses: Annotated[dict, merge1]
    errors: Annotated[list, operator.add]
class ROut(TypedDict, total=False):
    reports: Annotated[dict, merge]; theses: Annotated[dict, merge1]; errors: Annotated[list, operator.add]

lock = threading.Lock(); active = set(); peak = [0]; ctxseen = []; calls = []
CRASH = {"on": None}
def analyst(name):
    def f(s):
        with lock:
            active.add(s["symbol"]); peak[0] = max(peak[0], len(active)); calls.append((s["symbol"], name))
        ctxseen.append(CV.get())
        time.sleep(0.05)
        if CRASH["on"] == (s["symbol"], name):
            raise KeyboardInterrupt("simulated kill")
        return {"areports": {name: Rep(symbol=s["symbol"], analyst=name, d=s["as_of"])}}
    return f
def join(s): return {}
def director(s):
    with lock: active.discard(s["symbol"])
    return {"thesis": f"thesis-{s['symbol']}"}
def publish(s):
    return {"reports": {s["symbol"]: s["areports"]}, "theses": {s["symbol"]: s["thesis"]}}

sg = StateGraph(R, input_schema=RIn, output_schema=ROut)
for n in ["fund", "tech", "news"]:
    sg.add_node(n, analyst(n)); sg.add_edge(START, n)
sg.add_node("join", join); sg.add_edge(["fund","tech","news"], "join")
sg.add_node("director", director); sg.add_edge("join","director")
sg.add_node("publish", publish); sg.add_edge("director","publish"); sg.add_edge("publish", END)
sub = sg.compile()

def fan(s): return [Send("research", {"as_of": s["as_of"], "symbol": x}) for x in s["queue"]]
g = StateGraph(Fund)
g.add_node("prepare", lambda s: {})
g.add_node("research", sub)
g.add_node("collect", lambda s: {})
g.add_edge(START, "prepare"); g.add_conditional_edges("prepare", fan, ["research"]); g.add_edge("research", "collect"); g.add_edge("collect", END)

conn = sqlite3.connect("ck.sqlite", check_same_thread=False)
saver = SqliteSaver(conn)
app = g.compile(checkpointer=saver)
print(app.get_graph(xray=1).draw_mermaid())
syms = ["A","B","C","D","E"]
cfg = {"configurable": {"thread_id": "run1"}, "max_concurrency": 2}
CV.set("ctx!")
CRASH["on"] = ("D", "tech")
try:
    app.invoke({"as_of": date(2024,1,2), "queue": syms}, cfg, durability="sync")
except BaseException as e:
    print("crashed:", type(e).__name__, e)
print("calls before resume", len(calls), sorted(set(c[0] for c in calls)), "peak", peak[0])
print("ctx seen", set(ctxseen))
CRASH["on"] = None; calls.clear(); active.clear(); peak[0]=0
st = app.get_state(cfg)
print("next", st.next, "theses so far", st.values.get("theses"))
out = app.invoke(None, cfg, durability="sync")
print("calls on resume", sorted(calls))
print("final theses", out["theses"]); print(type(next(iter(out["reports"]["A"].values()))))
# fresh process-like reload
saver2 = SqliteSaver(sqlite3.connect("ck.sqlite", check_same_thread=False))
app2 = g.compile(checkpointer=saver2)
print("reloaded", app2.get_state(cfg).values["reports"]["A"]["fund"])
