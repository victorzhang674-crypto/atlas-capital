"""A research run that blocks mid-way so a test can kill its process (Stage 28 Task 6).

Usage: `python kill_worker.py <lake_path> <run_id> <log_path> <blocked_symbol> <symbols,...>`.
Runs the stub research graph checkpointed under `<lake_path>/runs/<run_id>/`; every agent call
appends `<symbol> <node> start|done` to `<log_path>`. The news analyst of `blocked_symbol`
logs `<symbol> news blocked` and then sleeps until the process is killed.
"""

import os
import sys
import time
from pathlib import Path

from graph_world import DAY, Spy

from atlas.orchestration.checkpoint import run_graph
from atlas.orchestration.research_graph import build_research_graph


def main() -> None:
    lake, run_id, log_path, blocked, symbols = sys.argv[1:6]
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)

    def log(line: str) -> None:
        os.write(fd, f"{line}\n".encode())

    def block() -> None:
        log(f"{blocked} news blocked")
        time.sleep(600)

    class LoggingSpy(Spy):
        def _call(self, task, node, make):  # type: ignore[no-untyped-def]
            log(f"{task.symbol} {node} start")
            out = super()._call(task, node, make)
            log(f"{task.symbol} {node} done")
            return out

    spy = LoggingSpy(hooks={(blocked, "news"): block})
    graph = build_research_graph(spy.team(), max_concurrency=2)
    run_graph(
        graph,
        {"as_of": DAY, "research_queue": symbols.split(",")},
        run_id=run_id,
        lake_path=Path(lake),
    )
    log("finished")


if __name__ == "__main__":
    main()
