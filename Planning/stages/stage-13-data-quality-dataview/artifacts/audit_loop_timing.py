"""Stage 13 audit A1: time the bar-window filter and the new-facts filter (500 symbols).

Usage: python audit_loop_timing.py <label>. Prints the median of 5 runs of each, in ms.
"""

import statistics
import sys
import time
from datetime import UTC, date, datetime, timedelta

import pandas as pd

sys.path.insert(0, "tests/unit/data")
from view_helpers import make_bars, sessions_ending  # noqa: E402

from atlas.data import quality  # noqa: E402

END = date(2024, 2, 29)
SINCE = sessions_ending(END, 21)[0]
bars = pd.concat(
    [make_bars(f"S{i:03d}", [100.0] * 90, end=END) for i in range(500)], ignore_index=True
)
stamps = [datetime(2023, 1, 1, 21, tzinfo=UTC) + timedelta(days=i % 420) for i in range(30_000)]
facts = pd.DataFrame(
    {"available_at": pd.to_datetime(pd.Series(stamps, dtype="object"), utc=True)}
).astype({"available_at": "datetime64[us, UTC]"})


def median_ms(fn) -> float:
    runs = []
    for _ in range(5):
        start = time.perf_counter()
        fn()
        runs.append((time.perf_counter() - start) * 1000)
    return statistics.median(runs)


window = median_ms(lambda: quality._in_window(bars, SINCE))
new_facts = median_ms(lambda: quality._known_since(facts, SINCE))
print(f"{sys.argv[1]}: _in_window on {len(bars)} bars = {window:.1f} ms; "
      f"new-facts filter on {len(facts)} facts = {new_facts:.1f} ms")
