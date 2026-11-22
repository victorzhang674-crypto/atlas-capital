"""Stage 29 follow-up 3: statistics and going-concern review of the 10-K + 10-Q filing_text
table in the scratch lake, read in record batches (a whole-table Lake.read_table overflows
Arrow's 2 GB string offsets once the text column passes 2 GB)."""
import collections, os, sys
from pathlib import Path
import pandas as pd
import pyarrow.dataset as ds
import atlas.cli.app  # noqa: F401
from atlas.core.storage import Lake
from atlas.core.config import RedFlagsConfig
from atlas.agents.tools.fundamental_tools import going_concern_rule, going_concern_sentences

cfg = RedFlagsConfig()
lake = Lake(Path(os.environ["TMPDIR"]) / "mda" / "full-lake")
files = [str(f) for f in lake.table_files("filing_text")]
d = ds.dataset(files, format="parquet")
meta = d.to_table(columns=["symbol", "form", "filed", "accepted_at", "status", "source", "chars"]).to_pandas()
print(f"rows {len(meta)}, symbols {meta.symbol.nunique()}, files {len(files)}")
print("forms", dict(collections.Counter(meta.form)))
print("status by form", dict(collections.Counter(zip(meta.form, meta.status))))
print("source of ok", dict(collections.Counter(meta[meta.status == "ok"].source)))
ok = meta[meta.status == "ok"]
for base in ("10-K", "10-Q"):
    o = ok[ok.form.str.startswith(base)]
    print(f"{base} MD&A chars: median {int(o.chars.median())} p5 {int(o.chars.quantile(.05))} p95 {int(o.chars.quantile(.95))} max {int(o.chars.max())}")
print("filed", meta.filed.min(), "..", meta.filed.max(), "| accepted_at missing:", int(meta.accepted_at.isna().sum()))
nf = meta[meta.status != "ok"]
print("not ok by year:", dict(sorted(collections.Counter(pd.to_datetime(nf.filed).dt.year).items())))
print("not ok by symbol (top 15):", collections.Counter(nf.symbol).most_common(15))
print("symbols with no ok MD&A at all:", sorted(set(meta.symbol) - set(ok.symbol)))
print("symbols with no ok 10-K MD&A:", sorted(set(meta.symbol) - set(ok[ok.form.str.startswith("10-K")].symbol)))
seven = ["AEP", "CLX", "MOS", "TWC", "TWX", "WFC", "WIN"]
for s in seven:
    m = meta[meta.symbol == s]
    print(f"  {s}: " + ", ".join(f"{f} {st} {n}" for (f, st), n in sorted(collections.Counter(zip(m.form, m.status)).items())))

both, flags, ex, nok = 0, [], collections.Counter(), 0
cols = ["symbol", "form", "filed", "status", "text"]
for batch in d.to_batches(columns=cols, filter=ds.field("status") == "ok", batch_size=256):
    for x in batch.to_pylist():
        nok += 1
        t = x["text"]
        all_both = going_concern_sentences(t, cfg.going_concern_terms, ())
        if not all_both:
            continue
        both += 1
        f = going_concern_rule(t, cfg)
        if f.status == "FLAG":
            flags.append((x["symbol"], x["filed"], x["form"], f.detail[:240]))
        kept = set(going_concern_sentences(t, cfg.going_concern_terms, cfg.going_concern_exclusions))
        for s in all_both:
            if s not in kept:
                ex[s[:180]] += 1
print(f"ok MD&As read {nok}; with a sentence holding both terms: {both}; FLAG under the rule:")
for sym, filed, form, det in sorted(flags):
    print(f"  {sym} {form} filed {filed} | {det}")
print(f"FLAG {len(flags)} of {nok} MD&As ({len({f[0] for f in flags})} symbols)")
print(f"excluded both-term sentences (distinct {len(ex)}):")
for s, k in ex.most_common():
    print(f"  {k} | {s}")
