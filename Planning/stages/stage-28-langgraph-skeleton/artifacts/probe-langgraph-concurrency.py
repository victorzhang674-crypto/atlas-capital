import operator, threading, time
from datetime import date
from typing import Annotated, TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
def merge1(a, b):
    out = dict(a or {}); out.update(b or {}); return out
class R(TypedDict, total=False):
    symbol: str
    a: Annotated[dict, merge1]
class F(TypedDict, total=False):
    queue: list
    a: Annotated[dict, merge1]
lock = threading.Lock(); cur=[0]; peak=[0]; per={}; perpeak={}
def an(n):
    def f(s):
        with lock:
            cur[0]+=1; peak[0]=max(peak[0],cur[0]); per[s["symbol"]]=per.get(s["symbol"],0)+1; perpeak[s["symbol"]]=max(perpeak.get(s["symbol"],0),per[s["symbol"]])
        time.sleep(0.1)
        with lock: cur[0]-=1; per[s["symbol"]]-=1
        return {"a": {s["symbol"]+n: 1}}
    return f
def build(inner_limit):
    sg = StateGraph(R)
    for n in "xyzw": sg.add_node(n, an(n)); sg.add_edge(START, n); sg.add_edge(n, END)
    sub = sg.compile()
    if inner_limit is not None: sub = sub.with_config(max_concurrency=inner_limit)
    g = StateGraph(F); g.add_node("research", sub)
    g.add_conditional_edges(START, lambda s: [Send("research", {"symbol": x}) for x in s["queue"]], ["research"]); g.add_edge("research", END)
    return g.compile()
for outer, inner in [(2, None), (2, 4), (3, 1), (None, None)]:
    cur[0]=0; peak[0]=0; per.clear(); perpeak.clear()
    app = build(inner); t=time.perf_counter()
    cfg = {"max_concurrency": outer} if outer else {}
    out = app.invoke({"queue": list("ABCDEF")}, cfg)
    print(outer, inner, "peak", peak[0], "perpeak", max(perpeak.values()), "n", len(out["a"]), f"{time.perf_counter()-t:.2f}s")
# empty send list
g = StateGraph(F); g.add_node("research", lambda s: {}); g.add_node("collect", lambda s: {"a": {"c":1}})
g.add_conditional_edges(START, lambda s: [Send("research", {"symbol": x}) for x in s["queue"]] or "collect", ["research","collect"]); g.add_edge("research","collect"); g.add_edge("collect", END)
print(g.compile().invoke({"queue": []}))
