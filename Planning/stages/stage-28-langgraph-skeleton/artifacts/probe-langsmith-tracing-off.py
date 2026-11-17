import os, socket, sys, time
sys.path.insert(0, "tests/unit/orchestration")
attempts = []
def connect(self, addr):
    attempts.append(addr); raise OSError("blocked by probe")
socket.socket.connect = connect
os.environ["LANGSMITH_TRACING"] = "true"; os.environ["LANGSMITH_API_KEY"] = "lsv2_fake"; os.environ["LANGSMITH_ENDPOINT"] = "http://127.0.0.1:9"
from graph_world import DAY
from atlas.orchestration import build_research_graph, stub_team
g = build_research_graph(stub_team(), max_concurrency=4)
from langsmith.run_helpers import tracing_context
with tracing_context(enabled=False):
    out = g.invoke({"as_of": DAY, "research_queue": ["CCC","AAA","BBB"]})
time.sleep(3)
print(sys.argv[1], "connect attempts", len(attempts))
