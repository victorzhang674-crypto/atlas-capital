import os, collections, pandas as pd
d = pd.read_parquet(os.environ["TMPDIR"]+"/s29f4/recheck.parquet")
d["text"] = [t if isinstance(t, str) else None for t in d["text"]]; d["old_text"] = [t if isinstance(t, str) else None for t in d["old_text"]]
c = d[~d.same].copy()
def S(t): return t if isinstance(t, str) else ""
def kind(r):
    o, n = S(r.old_text), S(r.text)
    if not o: return "found (was none)"
    if not n: return "LOST"
    if len(n) < len(o) and o.startswith(n[:300]): return "shorter, same start"
    if len(n) > len(o) and n.startswith(o[:300]): return "longer, same start"
    return "different start"
c["kind"] = c.apply(kind, axis=1)
print(pd.crosstab(c.why, c.kind).to_string())
def nextline(r):
    o, n = S(r.old_text), S(r.text)
    tail = n[-200:]
    i = o.find(tail)
    return o[i+len(tail):i+len(tail)+120].strip().split("\n")[0] if i >= 0 else "?"
def lastline(n): return n.rstrip().rsplit("\n",1)[-1][:60]
s = c[c.kind=="shorter, same start"]
print("SHORTER: line now ending the section (first 45 chars):")
for k,v in collections.Counter(nextline(r)[:45] for r in s.itertuples()).most_common(45): print(f"  {v:4d} {k}")
l = c[c.kind=="longer, same start"]
print("LONGER: symbols", collections.Counter(l.symbol).most_common(30))
x = c[c.kind=="different start"]
print("DIFFERENT START:")
for r in x.itertuples(): print(f"  {r.symbol} {r.form} {r.filed} {r.old_chars}->{r.chars} {r.source} | {S(r.old_text)[:60]!r} -> {S(r.text)[:60]!r}")
c.drop(columns=["text","old_text"]).to_csv(os.environ["TMPDIR"]+"/s29f4/changed.csv", index=False)
