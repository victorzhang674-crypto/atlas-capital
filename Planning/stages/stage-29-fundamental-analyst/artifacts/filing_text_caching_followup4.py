"""An EdgarClient stand-in that keeps fetched filing documents gzipped on disk."""
import gzip, json
from pathlib import Path

class Caching:
    def __init__(self, real, cache: Path, offline: bool = False):
        self.real, self.cache, self.offline = real, cache, offline
    def __getattr__(self, name):  # submissions, submissions_page, ...
        return getattr(self.real, name)
    def filing_documents(self, cik, accn):
        p = self.cache / accn / "_index.json"
        if not p.exists():
            if self.offline: raise RuntimeError(f"not cached {p}")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(self.real.filing_documents(cik, accn)))
        return tuple(tuple(x) for x in json.loads(p.read_text()))
    def filing_document(self, cik, accn, name):
        p = self.cache / accn / (name + ".gz")
        raw = self.cache / accn / name
        if raw.exists(): return raw.read_bytes()
        if not p.exists():
            if self.offline: raise RuntimeError(f"not cached {p}")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(gzip.compress(self.real.filing_document(cik, accn, name), 6))
        return gzip.decompress(p.read_bytes())
