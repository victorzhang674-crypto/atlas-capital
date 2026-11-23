"""Rows of the main lake's filing_text the follow-up 4 rules could change, found from the stored
text, plus a random control sample of the rest."""
import os, re
from pathlib import Path
import numpy as np, pandas as pd, pyarrow.dataset as ds
from atlas.data.filings import mda as M
MAIN = Path.home() / "Projects/atlas-capital/data/curated/filing_text"
base = Path(os.environ["TMPDIR"]) / "s29f4"
cols = ["symbol","cik","accn","form","status","chars","document","filed","source"]
rows, reasons = [], {}
item1 = re.compile(M._ITEM_2_END.pattern, re.I | re.M)
start_item = re.compile(r"^\s*items?\b", re.I)
for batch in ds.dataset(str(MAIN), format="parquet").to_batches(columns=cols + ["text"], batch_size=500):
    df = batch.to_pandas()
    for r in df.itertuples():
        why = []
        if r.status != "ok" or r.chars < 15000: why.append("problem")
        else:
            t = r.text
            if r.form.startswith("10-Q") and any(m.group(0).strip().lower().startswith("item") and re.match(r"\s*item\s*1(?![0-9a-z])", m.group(0), re.I) for m in M._ITEM_2_END.finditer(t)):
                why.append("inner_item1")
            last = t.rstrip().rsplit("\n", 1)[-1].strip()
            if M._OPEN_WORD.fullmatch(last): why.append("open_last_line")
            if r.source != "primary" or not start_item.match(t): why.append("exhibit_or_fallback")
        rows.append({c: getattr(r, c) for c in cols} | {"why": "+".join(why)})
t = pd.DataFrame(rows)
print(len(t), "rows")
print(t.why.replace("", "none").value_counts().to_string())
cand = t[t.why != ""]
rest = t[t.why == ""]
ctrl = rest.sample(n=600, random_state=29004)
cand.to_csv(base / "cand.csv", index=False)
ctrl.assign(why="control").to_csv(base / "ctrl.csv", index=False)
print("candidates", len(cand), "control", len(ctrl))
print(cand[cand.why!="problem"].groupby("why").symbol.agg(lambda s: ", ".join(sorted(set(s))[:25])).to_string())
