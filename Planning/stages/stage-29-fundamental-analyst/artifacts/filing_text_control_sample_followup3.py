"""Stage 29 follow-up 3 (H8): the control sample. 200 random filing_text rows that the H6-H7
fixes were not expected to change (not re-fetched) are extracted again with the current
`_mda_of` (documents fetched from EDGAR again) and compared with the stored round-1 rows."""
import collections, json, os, sys
from pathlib import Path
import pandas as pd
import pyarrow.dataset as ds
import atlas.cli.app  # noqa: F401
from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.fundamentals.edgar_client import build_edgar_client
from atlas.data.filings.text import _mda_of

root = Path(os.environ["TMPDIR"]) / "mda"
sample = {tuple(x) for x in json.loads((root / "control-sample.json").read_text())}
accns = sorted(a for _, a in sample)
d = ds.dataset(sorted(str(f) for f in (root / "round1" / "filing_text").rglob("*.parquet")), format="parquet")
cols = ["symbol", "cik", "accn", "form", "source", "document", "status", "chars", "text", "sha256"]
old = d.to_table(columns=cols, filter=ds.field("accn").isin(accns)).to_pandas()
old = old[[(s, a) in sample for s, a in zip(old.symbol, old.accn)]]
print(f"control rows found in round 1: {len(old)} of {len(sample)}")
print("forms", dict(collections.Counter(old.form)), "| sources", dict(collections.Counter(old.source)))
cfg = load_config()
client = build_edgar_client(cfg)
same, diff, doc_changed = 0, [], 0
for _, r in old.sort_values(["symbol", "accn"]).iterrows():
    filing = pd.Series({"accn": r.accn, "form": r.form, "primary_document": r.document})
    found, source, document, body = _mda_of(client, int(r.cik), filing, cfg.data.filing_text)
    import hashlib
    if hashlib.sha256(body).hexdigest() != r.sha256:
        doc_changed += 1
    if (found.status, source, document, found.text) == (r.status, r.source, r.document, r.text):
        same += 1
    else:
        diff.append((r.symbol, r.accn, r.form, r.status, found.status, r.source, source, r.chars, len(found.text or "")))
print(f"identical (status, source, document, text): {same} of {len(old)}")
print(f"documents whose bytes differ from round 1: {doc_changed}")
for x in diff:
    print("DIFF", x)
