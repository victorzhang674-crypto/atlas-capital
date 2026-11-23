"""Compare two full re-extraction outputs (A, B; parquet part folders) row by row, with the main
lake's stored text standing for rows marked `same`. Prints the kinds of B-vs-A changes."""
import collections, sys
from pathlib import Path
import pandas as pd, pyarrow.dataset as ds
sys.path.insert(0, str(Path(__file__).parent))  # filing_text_markers_followup5.py
from filing_text_markers_followup5 import hits
def load(d):
    return pd.concat([pd.read_parquet(f) for f in sorted(Path(d).glob('part-*.parquet'))], ignore_index=True)
A, B = load(sys.argv[1]), load(sys.argv[2])
keys = set(zip(B.symbol, B.accn)) & set(zip(A.symbol, A.accn))
MAIN = Path.home() / 'Projects/atlas-capital/data/curated/filing_text'
st = ds.dataset(str(MAIN), format='parquet').to_table(columns=['symbol', 'accn', 'text'], filter=ds.field('accn').isin(sorted({k[1] for k in keys}))).to_pandas()
stored = {(s, a): (t if isinstance(t, str) else '') for s, a, t in zip(st.symbol, st.accn, st.text)}
def texts(F):
    return {(r.symbol, r.accn): (stored.get((r.symbol, r.accn), '') if r.same else (r.text if isinstance(r.text, str) else '')) for r in F.itertuples()}
ta, tb = texts(A), texts(B)
meta = {(r.symbol, r.accn): r for r in B.itertuples()}
rows, kinds = [], collections.Counter()
for k in sorted(keys):
    o, n = ta[k], tb[k]
    if o == n: continue
    if not o: kind = 'found'
    elif not n: kind = 'LOST'
    elif len(n) < len(o) and o.startswith(n[:200]): kind = 'shorter'
    elif len(n) > len(o) and n.startswith(o[:200]): kind = 'longer'
    else: kind = 'other start'
    kinds[kind] += 1
    m = meta[k]
    rows.append((kind, k[0], m.form, m.filed, len(o), len(n), m.source, int(bool(hits(o))), int(bool(hits(n))), n[:70], n.rstrip()[-80:], o[len(n):len(n) + 80] if kind == 'shorter' else (o[:70] if kind == 'other start' else '')))
print('rows compared', len(keys), 'changed', len(rows), dict(kinds))
print('rows with markers: A', sum(bool(hits(t)) for k, t in ta.items() if k in keys), 'B', sum(bool(hits(t)) for k, t in tb.items() if k in keys))
for r in sorted(rows):
    print(f"{r[0]:11} {r[1]:5} {r[2]:5} {r[3]} {r[4]:>7}->{r[5]:<7} {r[6]:7} mk {r[7]}{r[8]} | {r[9]!r} ... END {r[10]!r} | {'CUT' if r[0]=='shorter' else 'WAS'} {r[11]!r}")
