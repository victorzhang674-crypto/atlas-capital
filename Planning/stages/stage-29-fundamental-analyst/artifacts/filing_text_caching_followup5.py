"""An EdgarClient stand-in that keeps fetched filing documents gzipped on disk (follow-up 5).

Reads a document from any of `extra` caches first (follow-up 4's), else from its own `cache`,
else fetches it (unless `offline`) and stores it in `cache`."""
import gzip, json
from pathlib import Path


class Caching:
    def __init__(self, real, cache: Path, extra: tuple[Path, ...] = (), offline: bool = False):
        self.real, self.cache, self.extra, self.offline = real, cache, extra, offline

    def __getattr__(self, name):  # submissions, submissions_page, ...
        return getattr(self.real, name)

    def _find(self, accn, name):
        for root in (*self.extra, self.cache):
            if (root / accn / name).exists():
                return root / accn / name
        return None

    def filing_documents(self, cik, accn):
        p = self._find(accn, "_index.json")
        if p is None:
            if self.offline:
                raise RuntimeError(f"not cached {accn}/_index.json")
            p = self.cache / accn / "_index.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(self.real.filing_documents(cik, accn)))
        return tuple(tuple(x) for x in json.loads(p.read_text()))

    def filing_document(self, cik, accn, name):
        raw = self._find(accn, name)
        if raw is not None:
            return raw.read_bytes()
        p = self._find(accn, name + ".gz")
        if p is None:
            if self.offline:
                raise RuntimeError(f"not cached {accn}/{name}")
            p = self.cache / accn / (name + ".gz")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(gzip.compress(self.real.filing_document(cik, accn, name), 6))
        return gzip.decompress(p.read_bytes())
