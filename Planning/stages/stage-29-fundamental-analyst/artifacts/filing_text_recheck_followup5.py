"""Re-extract the selected main-lake filing_text rows with the current code and compare them
with the stored text (follow-up 5).

usage: filing_text_recheck_followup5.py OUT.parquet SEL.csv [SEL.csv ...] [--offline]
Each SEL.csv has the columns symbol, cik, accn, form, filed, document, status, source, chars,
why. Documents are read from the follow-up 4 and 5 caches, else fetched from EDGAR."""
import importlib.util, os, sys, time
from pathlib import Path
import pandas as pd, pyarrow.dataset as ds

here = Path(__file__).parent
spec = importlib.util.spec_from_file_location("caching", here / "filing_text_caching_followup5.py")
caching = importlib.util.module_from_spec(spec); spec.loader.exec_module(caching)
import atlas.cli.app  # noqa: F401
from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.filings import text as T
from atlas.data.fundamentals import build_edgar_client

tmp = Path(os.environ["TMPDIR"])
base = tmp / "s29f5"
MAIN = Path.home() / "Projects/atlas-capital/data/curated/filing_text"
args = [a for a in sys.argv[1:] if not a.startswith("--")]
offline = "--offline" in sys.argv
out_path, sels = Path(args[0]), args[1:]
sel = pd.concat([pd.read_csv(p) for p in sels], ignore_index=True).drop_duplicates(["symbol", "accn"])
old = ds.dataset(str(MAIN), format="parquet").to_table(
    columns=["symbol", "accn", "text"], filter=ds.field("accn").isin(list(sel.accn))).to_pandas()
old = dict(zip(zip(old["symbol"], old["accn"]), old["text"]))
root = base / "probe-lake"
cfg = load_config("dev", overrides={"data": {"lake_path": str(root)}})
out, t0 = [], time.time()
with build_edgar_client(cfg, Lake(root)) as real:
    c = caching.Caching(real, base / "cache", extra=(tmp / "s29f4" / "cache",), offline=offline)
    for i, r in enumerate(sel.itertuples()):
        filing = pd.Series({"accn": r.accn, "form": r.form, "primary_document": r.document})
        o = old.get((r.symbol, r.accn))
        o = o if isinstance(o, str) else None
        rec = dict(symbol=r.symbol, cik=r.cik, form=r.form, filed=r.filed, accn=r.accn, why=r.why,
                   old_status=r.status, old_source=r.source, old_chars=r.chars, old_text=o)
        for attempt in range(3):
            try:
                found, source, doc, body = T._mda_of(c, int(r.cik), filing, cfg.data.filing_text)
                break
            except Exception as exc:  # an EDGAR outage: retried, then recorded; the run goes on
                err = exc
                time.sleep(2 * (attempt + 1))
        else:
            out.append(rec | dict(status="error", source=str(err)[:200], chars=0, same=False, text=None))
            continue
        out.append(rec | dict(status=found.status, source=source, chars=len(found.text or ""),
                              same=(found.text == o), text=found.text))
        if i % 100 == 0:
            print(i, len(sel), round(time.time() - t0), flush=True)
d = pd.DataFrame(out)
d.to_parquet(out_path)
print("done", len(d), "same", int(d.same.sum()), "errors", int((d.status == "error").sum()))
print(d.assign(changed=~d.same).groupby("why").changed.agg(["size", "sum"]).to_string())
