"""Stage 29 follow-up 3 (H8): the scratch filing_text table after the re-fetch, compared row by
row (by accession) with round 1 (before the H6-H7 fixes)."""
import collections, os
from pathlib import Path
import pyarrow.dataset as ds
root = Path(os.environ["TMPDIR"]) / "mda"
cols = ["symbol", "accn", "form", "status", "source", "chars", "text"]
def load(path):
    d = ds.dataset(sorted(str(f) for f in path.rglob("*.parquet")), format="parquet")
    out = {}
    for b in d.to_batches(columns=cols, batch_size=512):
        for x in b.to_pylist():
            out[(x["symbol"], x["accn"])] = x
    return out
a = load(root / "round1" / "filing_text"); b = load(root / "full-lake" / "curated" / "filing_text")
print(f"rows round 1 {len(a)}, final {len(b)}; keys only in round 1 {len(a.keys() - b.keys())}, only in final {len(b.keys() - a.keys())}")
c = collections.Counter()
by_sym = collections.Counter()
for k in a.keys() & b.keys():
    x, y = a[k], b[k]
    base = x["form"][:4]
    if x["text"] == y["text"] and x["status"] == y["status"] and x["source"] == y["source"]:
        c[(base, "unchanged")] += 1
        continue
    by_sym[k[0]] += 1
    if x["status"] != y["status"]:
        c[(base, f"status {x['status']} -> {y['status']}")] += 1
    elif x["source"] != y["source"]:
        c[(base, f"source {x['source']} -> {y['source']}")] += 1
    elif y["chars"] > x["chars"]:
        c[(base, "text longer")] += 1
    else:
        c[(base, "text shorter")] += 1
for k, n in sorted(c.items()):
    print(f"  {k[0]} {k[1]}: {n}")
print("changed rows by symbol (top 20):", by_sym.most_common(20))
for s in ("SO", "GAS", "ETR", "TSS", "SRE", "EMR", "GE", "IBM", "CLX"):
    rs = [k for k in b if k[0] == s]
    ch = sum(1 for k in rs if k in a and a[k]["text"] != b[k]["text"])
    med = sorted(b[k]["chars"] for k in rs)[len(rs) // 2] if rs else 0
    short = sum(1 for k in rs if b[k]["status"] == "ok" and b[k]["chars"] < 15000)
    print(f"  {s}: rows {len(rs)}, text changed {ch}, median chars {med}, ok under 15,000 chars {short}")
fin_short = collections.Counter(x["form"][:4] for x in b.values() if x["status"] == "ok" and x["chars"] < 15000)
print("final ok rows under short_mda_chars (15,000):", dict(fin_short))
print("10-Q rows ok in round 1, not found now:")
for k in sorted(a.keys() & b.keys()):
    if a[k]["status"] == "ok" and b[k]["status"] != "ok":
        print("  ", k, a[k]["form"], a[k]["chars"], repr((a[k]["text"] or "")[:300]))
print("final ok rows under 15,000 chars, 10-K:")
for k, y in sorted(b.items()):
    if y["status"] == "ok" and y["chars"] < 15000 and y["form"].startswith("10-K"):
        print("  ", k, y["form"], y["source"], y["chars"], repr((y["text"] or "")[:120]))
