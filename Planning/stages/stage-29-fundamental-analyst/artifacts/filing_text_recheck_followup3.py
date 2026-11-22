"""Stage 29 follow-up 3 (H9): after the "Managment" heading fix, re-extract with the final code
(documents fetched again) every row without an MD&A, every row whose text holds "managment",
and a fresh random sample of 300 rows of the final scratch table; print the rows that differ."""
import collections, hashlib, json, os, random, re
from pathlib import Path
import pandas as pd
import pyarrow.dataset as ds
import atlas.cli.app  # noqa: F401
from atlas.core.config import load_config
from atlas.data.fundamentals.edgar_client import build_edgar_client
from atlas.data.filings.text import _mda_of

root = Path(os.environ["TMPDIR"]) / "mda"
d = ds.dataset(sorted(str(f) for f in (root / "full-lake" / "curated" / "filing_text").rglob("*.parquet")), format="parquet")
cols = ["symbol", "cik", "accn", "form", "source", "document", "status", "chars", "text", "sha256"]
rows = [x for b in d.to_batches(columns=cols, batch_size=512) for x in b.to_pylist()]
typo = re.compile(r"managment", re.I)
pick = {}
for x in rows:
    if x["status"] != "ok":
        pick[(x["symbol"], x["accn"])] = ("not ok", x)
    elif typo.search(x["text"] or ""):
        pick[(x["symbol"], x["accn"])] = ("managment in text", x)
rng = random.Random(30)
for x in rng.sample([x for x in rows if (x["symbol"], x["accn"]) not in pick], 300):
    pick[(x["symbol"], x["accn"])] = ("random", x)
print("rows re-extracted:", dict(collections.Counter(w for w, _ in pick.values())))
cfg = load_config()
client = build_edgar_client(cfg)
same, diff = collections.Counter(), []
for (sym, accn), (why, x) in sorted(pick.items()):
    if x["source"] != "primary":
        # the primary document's name is not stored for an exhibit row: list the filing's documents
        primary = next(n for k, n in client.filing_documents(int(x["cik"]), accn) if k.upper().startswith(x["form"][:4]))
    else:
        primary = x["document"]
    filing = pd.Series({"accn": accn, "form": x["form"], "primary_document": primary})
    found, source, document, body = _mda_of(client, int(x["cik"]), filing, cfg.data.filing_text)
    if (found.status, source, document, found.text) == (x["status"], x["source"], x["document"], x["text"]):
        same[why] += 1
    else:
        diff.append((sym, accn, x["form"], why, x["status"], found.status, x["source"], source, x["chars"], len(found.text or "")))
print("identical:", dict(same))
for t in diff:
    print("DIFF", t)
(root / "recheck4-diff.json").write_text(json.dumps([[t[0], t[1]] for t in diff]))
