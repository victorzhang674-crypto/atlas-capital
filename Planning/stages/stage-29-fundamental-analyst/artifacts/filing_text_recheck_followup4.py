"""Re-extract the candidate and control rows with the current code; compare with the main lake."""
import os, sys, time
from pathlib import Path
import pandas as pd, pyarrow.dataset as ds
sys.path.insert(0, str(Path(__file__).parent))
from caching import Caching
import atlas.cli.app  # noqa
from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.fundamentals import build_edgar_client
from atlas.data.filings import text as T
base = Path(os.environ["TMPDIR"]) / "s29f4"
MAIN = Path.home() / "Projects/atlas-capital/data/curated/filing_text"
offline = "--offline" in sys.argv
sel = pd.concat([pd.read_csv(base / n) for n in ("cand.csv", "ctrl.csv", "cand2.csv", "cand3.csv")], ignore_index=True)
old = ds.dataset(str(MAIN), format="parquet").to_table(
    columns=["symbol", "accn", "text"], filter=ds.field("accn").isin(list(sel.accn))).to_pandas()
old = dict(zip(zip(old["symbol"], old["accn"]), old["text"]))
root = base / "probe-lake"
cfg = load_config("dev", overrides={"data": {"lake_path": str(root)}})
out, t0 = [], time.time()
with build_edgar_client(cfg, Lake(root)) as real:
    c = Caching(real, base / "cache", offline=offline)
    for i, r in enumerate(sel.itertuples()):
        filing = pd.Series({"accn": r.accn, "form": r.form, "primary_document": r.document})
        try:
            found, source, doc, body = T._mda_of(c, int(r.cik), filing, cfg.data.filing_text)
        except Exception as exc:  # an EDGAR outage: recorded, the run goes on
            out.append(dict(symbol=r.symbol, form=r.form, filed=r.filed, accn=r.accn, why=r.why,
                            old_status=r.status, old_source=r.source, old_chars=r.chars,
                            status="error", source=str(exc)[:200], chars=0, same=False,
                            text=None, old_text=None))
            continue
        o = old.get((r.symbol, r.accn))
        o = None if isinstance(o, float) else o
        out.append(dict(symbol=r.symbol, form=r.form, filed=r.filed, accn=r.accn, why=r.why,
                        old_status=r.status, old_source=r.source, old_chars=r.chars,
                        status=found.status, source=source, chars=len(found.text or ""),
                        same=(found.text == o), text=found.text, old_text=o))
        if i % 100 == 0: print(i, len(sel), round(time.time() - t0), flush=True)
pd.DataFrame(out).to_parquet(base / "recheck.parquet")
d = pd.DataFrame(out)
print("done", len(d), "same", int(d.same.sum()))
print(d.assign(changed=~d.same).groupby("why").changed.agg(["size", "sum"]).to_string())
