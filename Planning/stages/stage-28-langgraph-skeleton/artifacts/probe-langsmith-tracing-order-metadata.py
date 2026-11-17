import os, socket, sys, time, sqlite3, random
sys.path.insert(0, "tests/unit/orchestration")
attempts = []
orig = socket.socket.connect
def connect(self, addr):
    attempts.append(addr); raise OSError("blocked by probe")
socket.socket.connect = connect
os.environ["LANGSMITH_TRACING"] = "true"; os.environ["LANGSMITH_API_KEY"] = "lsv2_fake"; os.environ["LANGSMITH_ENDPOINT"] = "http://127.0.0.1:9"
from graph_world import DAY, Spy
from atlas.orchestration import build_research_graph, stub_team, sqlite_checkpointer
g = build_research_graph(stub_team(), max_concurrency=4)
out = g.invoke({"as_of": DAY, "research_queue": ["CCC","AAA","BBB"]})
time.sleep(2)
print("tracing on: connect attempts", len(attempts), attempts[:2])
attempts.clear()
from langsmith.run_helpers import tracing_context
with tracing_context(enabled=False):
    out = g.invoke({"as_of": DAY, "research_queue": ["CCC","AAA","BBB"]})
time.sleep(2)
print("tracing_context(False): connect attempts", len(attempts))
# order of theses keys vs queue with random finishing order
for i in range(5):
    spy = Spy(hooks={(s, "director"): (lambda d=random.random()/20: time.sleep(d)) for s in ["EEE","AAA","DDD","BBB","CCC"]})
    q = ["EEE","AAA","DDD","BBB","CCC"]
    out = build_research_graph(spy.team(), max_concurrency=5).invoke({"as_of": DAY, "research_queue": q})
    print(list(out["theses"]), list(out["reports"]))
# metadata into checkpoints
from pathlib import Path
p = Path(os.environ["TMPDIR"])/"s28"/"meta.sqlite"; p.unlink(missing_ok=True)
with sqlite_checkpointer(p) as saver:
    cfg = g.config(thread_id="T1"); cfg["metadata"] = {"atlas_graph": "fp123"}
    g.compile(saver).invoke({"as_of": DAY, "research_queue": ["AAA"]}, cfg, durability="sync")
    t = saver.get_tuple({"configurable": {"thread_id": "T1"}})
    print("metadata", t.metadata)
