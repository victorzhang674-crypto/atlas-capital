"""Follow-up 5, first scan: every stored MD&A of the main lake (argument 1: its filing_text folder) checked for text that only follows an MD&A; rows with a marker written to argument 2 (CSV)."""
import re, sys
import pyarrow.dataset as ds
import pandas as pd
MAIN = sys.argv[1]
M = {
  "signatures": r"^[ \t]*signatures?[ \t]*$",
  "pursuant": r"^[ \t]*pursuant to the requirements of (?:section 13|the securities exchange act)",
  "item_9a+": r"^[ \t]*(?:part[ \t]+i{1,3}v?[ \t.,]*)?item[ \t]*(?:9a|9b|9c|1[0-6])(?![0-9a-z])",
  "item_6_10q": r"^[ \t]*item[ \t]*(?:5|6)(?![0-9a-z])[ \t.:\-–—]*(?:$|exhibits|other)",
  "exhibit_index": r"^[ \t]*(?:exhibit[ \t]+index|index[ \t]+to[ \t]+exhibits)",
  "notes_to": r"^[ \t]*notes[ \t]+to[ \t]+(?:the[ \t]+)?(?:condensed[ \t]+)?consolidated[ \t]+financial[ \t]+statements[ \t]*$",
  "part_iii_iv": r"^[ \t]*part[ \t]+(?:iii|iv)(?![a-z])",
  "certify": r"^[ \t]*i,[ \t]+[^\n]{3,60},[ \t]+certify",
}
R = {k: re.compile(v, re.I | re.M) for k, v in M.items()}
d = ds.dataset(MAIN, format="parquet")
rows = []
for b in d.to_batches(columns=["symbol","accn","form","filed","source","chars","text"], batch_size=2000):
    f = b.to_pandas()
    for r in f.itertuples():
        if not isinstance(r.text, str): continue
        hits = {k: len(rx.findall(r.text)) for k, rx in R.items()}
        hits = {k: v for k, v in hits.items() if v}
        if hits:
            first = min(m.start() for rx in R.values() for m in [rx.search(r.text)] if m)
            rows.append(dict(symbol=r.symbol, accn=r.accn, form=r.form, filed=r.filed, source=r.source,
                             chars=r.chars, first_at=first, tail=r.chars-first, **hits))
out = pd.DataFrame(rows)
out.to_csv(sys.argv[2], index=False)
print(len(out))
for k in M:
    if k in out: print(k, int((out[k].fillna(0)>0).sum()))
