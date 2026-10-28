"""Is EDGAR's submissions `acceptanceDateTime` (suffix Z) UTC or US Eastern?

EDGAR gives a 10-K/10-Q/8-K accepted after 17:30 ET the next business day's filing date. For
each periodic/current report in the given submissions JSON files, check that rule reading the
timestamp as UTC and as Eastern; the reading that fits is the true zone.
Usage: python acceptance_utc_check.py <submissions JSON> ...   (full, untrimmed `recent` arrays)
"""

import json
import sys
from datetime import datetime, time
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A", "8-K"}

total = {"utc": [0, 0], "eastern": [0, 0]}
for path in sys.argv[1:]:
    recent = json.load(open(path))["filings"]["recent"]
    res = {"utc": [0, 0], "eastern": [0, 0]}
    misses = []
    for form, filed, acc in zip(recent["form"], recent["filingDate"], recent["acceptanceDateTime"]):
        if form not in FORMS:
            continue
        ts = datetime.fromisoformat(acc.replace("Z", "+00:00"))
        for name, local in (("utc", ts.astimezone(NY)), ("eastern", ts.replace(tzinfo=NY))):
            day = local.date().isoformat()
            fits = filed == day if local.time() <= time(17, 30) else filed > day
            res[name][0 if fits else 1] += 1
            total[name][0 if fits else 1] += 1
            if name == "utc" and not fits:
                misses.append((form, filed, acc))
    print(f"{path.rsplit('/', 1)[-1]}: fits as UTC {res['utc'][0]}/{sum(res['utc'])}, "
          f"as Eastern {res['eastern'][0]}/{sum(res['eastern'])}; UTC misses: {misses}")
print(f"TOTAL: fits as UTC {total['utc'][0]}/{sum(total['utc'])}, "
      f"as Eastern {total['eastern'][0]}/{sum(total['eastern'])}")
