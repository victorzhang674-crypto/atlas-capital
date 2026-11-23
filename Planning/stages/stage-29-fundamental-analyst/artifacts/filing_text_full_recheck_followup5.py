"""Re-extract EVERY main-lake filing_text row with the worktree's code (follow-up 5).

The follow-up 4 review re-read only the rows its rules could change; follow-up 5 found stored
rows that the current code extracts differently (rows extracted by earlier code), so every
row is read again. Each document's text (`html_to_text`, unchanged by follow-up 5) is kept
gzipped under `$TMPDIR/s29f5/txt/<accn>/`, with each filing's document index, so later runs
(`--offline`) re-extract the whole corpus without EDGAR.

usage: filing_text_full_recheck_followup5.py OUT_DIR [--offline] [--workers N] [--rate R] [--symbols A,B] [--cached-only] [--exhibit-rows]
(R: requests per second per worker; default 10 / N, the SEC's limit in all)
Writes OUT_DIR/part-*.parquet (one row per filing: the stored and new status, source, chars,
`same`, and the new text when it differs) and resumes: filings already in OUT_DIR are skipped."""
import gzip, json, os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds

import atlas.cli.app  # noqa: F401
from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.filings import mda as M
from atlas.data.filings import text as T
from atlas.data.fundamentals import build_edgar_client

tmp = Path(os.environ["TMPDIR"])
base = tmp / "s29f5"
TXT = base / "txt"
MAIN = Path.home() / "Projects/atlas-capital/data/curated/filing_text"
args = [a for a in sys.argv[1:2]]
offline = "--offline" in sys.argv
workers = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 4
rate = float(sys.argv[sys.argv.index("--rate") + 1]) if "--rate" in sys.argv else 10 / workers
out_dir = Path(args[0]); out_dir.mkdir(parents=True, exist_ok=True)


class Txt(bytes):
    """A document handed to `_mda_of` as its text (the cached `html_to_text` output)."""


_html_to_text = M.html_to_text
T.html_to_text = lambda body: body.text if isinstance(body, Txt) else _html_to_text(body)


class TextCache:
    """`filing_documents` / `filing_document` of an EdgarClient, kept as text on disk."""

    def __init__(self, real):
        self.real = real

    def __getattr__(self, name):
        return getattr(self.real, name)

    def filing_documents(self, cik, accn):
        p = TXT / accn / "_index.json"
        if not p.exists():
            if self.real is None:
                raise RuntimeError(f"not cached {accn}/_index.json")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(self.real.filing_documents(cik, accn)))
        return tuple(tuple(x) for x in json.loads(p.read_text()))

    def filing_document(self, cik, accn, name):
        p = TXT / accn / (name + ".txt.gz")
        if p.exists():
            text = gzip.decompress(p.read_bytes()).decode("utf-8")
        else:
            if self.real is None:
                raise RuntimeError(f"not cached {accn}/{name}")
            text = _html_to_text(self.real.filing_document(cik, accn, name))
            p.parent.mkdir(parents=True, exist_ok=True)
            tmpf = p.with_suffix(".part")
            tmpf.write_bytes(gzip.compress(text.encode("utf-8"), 6))
            tmpf.rename(p)
        out = Txt(b"")
        out.text = text
        return out


cols = ["symbol", "cik", "accn", "form", "filed", "document", "status", "source", "chars", "text"]
stored = ds.dataset(str(MAIN), format="parquet").to_table(columns=cols).to_pandas()
done = set()
for f in out_dir.glob("part-*.parquet"):
    done |= set(zip(*pd.read_parquet(f, columns=["symbol", "accn"]).to_dict("list").values()))
todo = stored[[(s, a) not in done for s, a in zip(stored.symbol, stored.accn)]]
if "--cached-only" in sys.argv:  # an offline pass over the rows whose document is cached
    todo = todo[[(TXT / a / (d + ".txt.gz")).exists() for a, d in zip(todo.accn, todo.document)]]
if "--exhibit-rows" in sys.argv:  # the rows whose stored MD&A came from an exhibit
    todo = todo[todo.source != "primary"]
if "--symbols" in sys.argv:
    todo = todo[todo.symbol.isin(sys.argv[sys.argv.index("--symbols") + 1].split(","))]
print(f"stored {len(stored)}, done {len(done)}, to do {len(todo)}, offline {offline}", flush=True)
cfg = load_config("dev", overrides={"data": {"lake_path": str(base / "probe-lake"),
                  "fundamentals": {"max_requests_per_second": rate}}})
local = threading.local()
clients, lock, results, t0 = [], threading.Lock(), [], time.time()
parts = len(list(out_dir.glob("part-*.parquet")))


def client():
    if not hasattr(local, "c"):
        real = None if offline else build_edgar_client(cfg, Lake(base / "probe-lake"))
        if real is not None:
            clients.append(real)
        local.c = TextCache(real)
    return local.c


def flush(force=False):
    global parts, results
    if results and (force or len(results) >= 500):
        pd.DataFrame(results).to_parquet(out_dir / f"part-{parts:04d}.parquet")
        parts += 1
        results = []


def _primary(r):
    """The filing's primary document. A row whose MD&A came from an exhibit stores the
    exhibit's name in `document`: the primary one is the filing index's first document of the
    filing's base form."""
    if r.source == "primary":
        return r.document
    base = "10-Q" if str(r.form).upper().startswith("10-Q") else "10-K"
    docs = client().filing_documents(int(r.cik), r.accn)
    return next((n for k, n in docs if k.upper().startswith(base)), r.document)


def one(r):
    old = r.text if isinstance(r.text, str) else None
    rec = dict(symbol=r.symbol, cik=int(r.cik), accn=r.accn, form=r.form, filed=r.filed,
               document=r.document, old_status=r.status, old_source=r.source, old_chars=int(r.chars))
    err, found = None, None
    for attempt in range(4):
        try:
            filing = pd.Series({"accn": r.accn, "form": r.form, "primary_document": _primary(r)})
            found, source, doc, _ = T._mda_of(client(), int(r.cik), filing, cfg.data.filing_text)
            break
        except Exception as exc:  # an EDGAR outage: retried, then recorded; the run goes on
            err = exc
            if offline:
                break
            time.sleep(3 * (attempt + 1))
    if found is None:
        rec |= dict(status="error", source=str(err)[:200], new_document="", chars=0, same=False, text=None)
    else:
        same = found.text == old and found.status == r.status and source == r.source
        rec |= dict(status=found.status, source=source, new_document=doc, chars=len(found.text or ""),
                    same=same, text=None if same else found.text)
    with lock:
        results.append(rec)
        flush()


rows = list(todo.itertuples())
with ThreadPoolExecutor(workers) as pool:
    for i, _ in enumerate(pool.map(one, rows)):
        if i % 500 == 0:
            print(i, len(rows), round(time.time() - t0), flush=True)
with lock:
    flush(force=True)
for c in clients:
    c.close()
d = pd.concat([pd.read_parquet(f) for f in sorted(out_dir.glob("part-*.parquet"))], ignore_index=True)
print("done", len(d), "same", int(d.same.sum()), "errors", int((d.status == "error").sum()), flush=True)
