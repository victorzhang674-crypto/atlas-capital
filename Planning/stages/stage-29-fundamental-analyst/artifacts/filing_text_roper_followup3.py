"""Stage 29 follow-up 3 (H9): fetch Roper's 2017-11 10-Q again in the scratch lake after the
"Managment" heading fix (its row is deleted, then ingest_filing_text fetches only it)."""
import os
from pathlib import Path
import pyarrow.dataset as ds
from atlas.core.config import load_config
from atlas.core.storage import Lake
import atlas.cli.app  # noqa: F401
from atlas.data.fundamentals import build_edgar_client, build_cik_map
from atlas.data.filings.text import ingest_filing_text, FILING_TEXT
root = Path(os.environ["TMPDIR"]) / "mda" / "full-lake"
cfg = load_config("dev", overrides={"data": {"lake_path": str(root)}})
lake = Lake(root)
accn = "0000882835-17-000031"
print("deleted", lake.delete_rows(FILING_TEXT.name, [("symbol", "==", "ROP"), ("accn", "==", accn)]))
with build_edgar_client(cfg, lake) as client:
    rep = ingest_filing_text(lake, client, ["ROP"], cik_map=build_cik_map(client, cfg), config=cfg, refresh=False)
print(f"rows={rep.rows} ok={rep.symbols_ok} failed={dict(rep.failure_reasons)} statuses={dict(rep.statuses)}")
d = ds.dataset(sorted(str(f) for f in (root / "curated" / "filing_text").rglob("*.parquet")), format="parquet")
t = d.to_table(columns=["symbol", "accn", "status", "chars"], filter=ds.field("symbol") == "ROP").to_pandas()
print("ROP rows", len(t), "|", t[t.accn == accn].to_dict("records"))
print("table rows", d.count_rows())
