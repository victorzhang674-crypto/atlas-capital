"""Phase 3 checkpoint probe: the DataView cache (`_LRU`, Stage 13) read from four threads.

Run before the fix (base 3900868): readers evict each other's keys between the cache's
"is it there" check and its use of the entry -> KeyError. After the fix: no error.
Usage: uv run python <this file>
"""

import sys
import threading

from atlas.data.view import _LRU

sys.setswitchinterval(1e-6)  # switch threads often, so the race shows within seconds
lru = _LRU(2)  # five keys through two slots
errors: list[str] = []


def work(seed: int) -> None:
    try:
        for i in range(200_000):
            key = (i + seed) % 5
            assert lru.get(key, lambda key=key: key * 10) == key * 10
    except Exception as exc:
        errors.append(repr(exc))


threads = [threading.Thread(target=work, args=(s,)) for s in range(4)]
for t in threads:
    t.start()
for t in threads:
    t.join()
print(f"reader threads that died: {len(errors)} of 4; errors: {errors}")
print("cache counters:", lru.info())
