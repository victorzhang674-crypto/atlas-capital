"""SEC EDGAR client: ticker → CIK map, companyfacts and submissions JSON, raw cache, and the
documents of a filing (Stage 29 follow-up 2).

EDGAR's fair-access policy asks for at most 10 requests per second and a User-Agent that
declares who is calling (with contact details). The client throttles every request to
`max_requests_per_second`, retries transport errors, HTTP 429 and 5xx answers
(`call_with_retry`, Stage 07), and fails at once on any other non-200 answer. The User-Agent
comes from the `SEC_USER_AGENT` secret (`build_edgar_client`) and is never logged.

With a lake, every JSON answer is archived under `raw/sec/<dataset>/` as received (written
atomically, only after it parsed as a JSON object): `tickers/company_tickers.json`,
`companyfacts/CIK##########.json`, `submissions/CIK##########.json` and the submissions history
pages. A cached file is served instead of a request unless `refresh=True`; history pages never
change and are always served from the cache once stored.
"""

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import TracebackType
from typing import Annotated, Any, BinaryIO, Self

import httpx
import lxml.html
import pandas as pd
from pydantic import Field, ValidationError, model_validator

from atlas.core.config import AtlasConfig, FundamentalsConfig, read_config_file
from atlas.core.errors import ConfigError, DataError
from atlas.core.logging import get_logger
from atlas.core.storage import Lake
from atlas.core.storage.meta import atomic_write
from atlas.core.types.base import AtlasModel, NonEmptyStr
from atlas.core.types.instruments import Symbol, normalize_symbol
from atlas.data.fundamentals.parser import parse_submissions
from atlas.data.providers.base import RetryPolicy, Throttle, call_with_retry

_log = get_logger(__name__)

RAW_VENDOR = "sec"
"""Vendor directory of the raw archive: `raw/sec/`."""


class _RetryableStatus(Exception):
    """An HTTP answer worth retrying (429 or 5xx)."""


@dataclass(frozen=True)
class CikSource:
    """A CIK whose filings belong to a symbol: those filed from `since` through `until` (both
    inclusive; `None` is open)."""

    cik: int
    since: date | None
    until: date | None

    def covers(self, filed: date) -> bool:
        """Whether a filing filed on `filed` is the symbol's."""
        return (self.since is None or filed >= self.since) and (
            self.until is None or filed <= self.until
        )


class CikMap:
    """Maps a ticker (any spelling `normalize_symbol` accepts) to the company's SEC CIK.

    Built from EDGAR's `company_tickers.json`, which lists **current** tickers only: a former
    member that no longer files, or an old ticker, is not found (`cik` returns `None`). With
    overrides, a symbol may also have former CIKs (an issuer that moved to a new CIK) and a
    first filing date for its current CIK (`sources`).
    """

    def __init__(
        self,
        ciks: Mapping[str, int],
        *,
        former: Mapping[str, tuple["FormerCik", ...]] | None = None,
        since: Mapping[str, date] | None = None,
    ) -> None:
        """A map from canonical symbols (`BRK.B`) to CIKs; `former` and `since` (canonical
        symbols too) as in `CikOverride`."""
        self._ciks = dict(ciks)
        self._former = dict(former or {})
        self._since = dict(since or {})

    @classmethod
    def from_company_tickers(cls, payload: Mapping[str, Any]) -> Self:
        """The map from a `company_tickers.json` payload (`{"0": {"cik_str", "ticker",
        "title"}, ...}`).

        Tickers are normalised (`BRK-B` → `BRK.B`); one that cannot be is skipped. A ticker
        listed for two CIKs keeps the first one (warning). Entries without an integer
        `cik_str` and a string `ticker` raise `DataError`.
        """
        ciks: dict[str, int] = {}
        skipped = 0
        for key, entry in payload.items():
            cik = entry.get("cik_str") if isinstance(entry, Mapping) else None
            ticker = entry.get("ticker") if isinstance(entry, Mapping) else None
            if not isinstance(cik, int) or isinstance(cik, bool) or not isinstance(ticker, str):
                raise DataError(f"company_tickers entry {key!r} lacks cik_str/ticker: {entry!r}")
            try:
                symbol = normalize_symbol(ticker)
            except ValueError:
                skipped += 1
                continue
            if symbol in ciks:
                if ciks[symbol] != cik:
                    _log.warning(
                        "fundamentals.duplicate_ticker", symbol=symbol, kept=ciks[symbol], cik=cik
                    )
                continue
            ciks[symbol] = cik
        if skipped:
            _log.info("fundamentals.tickers_skipped", count=skipped)
        return cls(ciks)

    def with_overrides(self, overrides: Sequence["CikOverride"]) -> "CikMap":
        """A new map where each override replaces its symbol's CIK, former CIKs and `since`
        (`cik=None` removes the symbol: no verified CIK). This map is unchanged."""
        ciks, former, since = dict(self._ciks), dict(self._former), dict(self._since)
        for override in overrides:
            former.pop(override.symbol, None)
            since.pop(override.symbol, None)
            if override.cik is None:
                ciks.pop(override.symbol, None)
                continue
            ciks[override.symbol] = override.cik
            if override.former:
                former[override.symbol] = override.former
            if override.since is not None:
                since[override.symbol] = override.since
        return CikMap(ciks, former=former, since=since)

    def sources(self, symbol: str) -> tuple[CikSource, ...]:
        """The CIKs whose filings are `symbol`'s: its current CIK (from its `since`, if any),
        then its former CIKs up to their `until`, newest first; none when `symbol` has no CIK
        (or is not a valid symbol). Stage 29 follow-up 4."""
        cik = self.cik(symbol)
        if cik is None:
            return ()
        key = normalize_symbol(symbol)
        own = CikSource(cik=cik, since=self._since.get(key), until=None)
        return (own, *(CikSource(f.cik, None, f.until) for f in self._former.get(key, ())))

    def cik(self, symbol: str) -> int | None:
        """The CIK of `symbol`, or `None` when it is not listed (or not a valid symbol)."""
        try:
            return self._ciks.get(normalize_symbol(symbol))
        except ValueError:
            return None

    def __len__(self) -> int:
        """Number of tickers in the map."""
        return len(self._ciks)


class FormerCik(AtlasModel):
    """An earlier CIK of a symbol's issuer (Stage 29 follow-up 4): ExxonMobil before its 2026
    holding company, Disney before 2019, or the earlier company of a reused ticker (SanDisk).
    Its filings filed up to `until` (inclusive: the day before the next CIK's first 10-K or
    10-Q) are the symbol's; later ones (a former issuer still filing as a subsidiary) are not.
    `name` is the SEC's name for it."""

    cik: Annotated[int, Field(gt=0)]
    until: date
    name: NonEmptyStr


class CikOverride(AtlasModel):
    """A verified CIK for a symbol `company_tickers.json` cannot resolve correctly (a former
    S&P 500 member: delisted, or its ticker now used by another company), or whose issuer
    filed under other CIKs before (Stage 29 follow-up 4).

    `cik=None` means no verified SEC CIK (e.g. a bank that filed with the FDIC). `name` is the
    change-log security name; `evidence` says how the CIK was checked. `former` lists the
    issuer's earlier CIKs, newest first; `since` is the first filing date of `cik` that is the
    symbol's (Viatris's CIK filed Upjohn's reports before the merger with Mylan).
    """

    symbol: Symbol
    cik: Annotated[int, Field(gt=0)] | None
    name: NonEmptyStr
    evidence: NonEmptyStr
    since: date | None = None
    former: tuple[FormerCik, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.cik is None and (self.former or self.since is not None):
            raise ValueError(f"{self.symbol}: former CIKs or since with no CIK (cik: null)")
        seen = {self.cik}
        for former in self.former:
            if former.cik in seen:
                which = "the current CIK" if former.cik == self.cik else "listed twice"
                raise ValueError(f"{self.symbol}: former CIK {former.cik} is {which}")
            seen.add(former.cik)
        return self


class _CikOverridesFile(AtlasModel):
    """Body of `config/cik_overrides.yaml`."""

    overrides: tuple[CikOverride, ...] = ()

    @model_validator(mode="after")
    def _unique(self) -> Self:
        seen: set[str] = set()
        for override in self.overrides:
            if override.symbol in seen:
                raise ValueError(f"{override.symbol} is listed twice")
            seen.add(override.symbol)
        return self


def load_cik_overrides(path: Path) -> tuple[CikOverride, ...]:
    """The overrides in `path` (`overrides: [...]`; see `config/cik_overrides.yaml`).

    A missing file gives none. Invalid content raises `ConfigError` naming the file and the
    entry at fault.
    """
    data = read_config_file(path)
    try:
        body = _CikOverridesFile.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigError(f"{path}: {problems}") from None
    return body.overrides


class EdgarClient:
    """Fetches EDGAR JSON with a declared User-Agent, a rate limit, retries and a raw cache.

    `lake` (optional) is where answers are archived (`raw/sec/`); `http` and `sleep` are
    injectable for tests. A blank `user_agent` raises `ConfigError`.
    """

    def __init__(
        self,
        config: FundamentalsConfig,
        *,
        user_agent: str,
        lake: Lake | None = None,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not user_agent.strip():
            raise ConfigError("EDGAR requires a User-Agent with contact details (SEC_USER_AGENT)")
        self._config = config
        self._lake = lake
        self._sleep = sleep
        self._policy = RetryPolicy(
            max_retries=config.max_retries,
            backoff_seconds=config.backoff_seconds,
            min_interval_seconds=1.0 / config.max_requests_per_second,
        )
        self._throttle = Throttle(self._policy.min_interval_seconds, sleep)
        self._owns_http = http is None
        self._http = (
            http
            if http is not None
            else httpx.Client(timeout=config.request_timeout_seconds, follow_redirects=True)
        )
        self._headers = {"User-Agent": user_agent}

    # --- context management --------------------------------------------------------------------

    def close(self) -> None:
        """Close the HTTP connection pool (only if this client created it)."""
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- endpoints -------------------------------------------------------------------------------

    def company_tickers(self, *, refresh: bool = False) -> dict[str, Any]:
        """`company_tickers.json`: every current ticker with its CIK and company name."""
        return self._get_json(
            self._config.company_tickers_url,
            self._cache("tickers", "company_tickers.json"),
            refresh=refresh,
        )

    def companyfacts(self, cik: int, *, refresh: bool = False) -> dict[str, Any]:
        """The XBRL companyfacts of `cik` (`{"cik", "entityName", "facts": {taxonomy: ...}}`)."""
        return self._get_json(
            self._config.companyfacts_url.format(cik=cik),
            self._cache("companyfacts", f"CIK{cik:010d}.json"),
            refresh=refresh,
        )

    def submissions(self, cik: int, *, refresh: bool = False) -> dict[str, Any]:
        """The submissions (filing index) of `cik`: `filings.recent` plus `filings.files`."""
        return self._get_json(
            self._config.submissions_url.format(cik=cik),
            self._cache("submissions", f"CIK{cik:010d}.json"),
            refresh=refresh,
        )

    def submissions_page(self, name: str) -> dict[str, Any]:
        """A submissions history page (`CIK##########-submissions-NNN.json`): column arrays
        like `filings.recent`. Pages never change, so a cached page is always reused."""
        if "/" in name or "\\" in name or not name.endswith(".json"):
            raise DataError(f"invalid submissions page name {name!r}")
        return self._get_json(
            self._config.submissions_page_url.format(name=name),
            self._cache("submissions", name),
            refresh=False,
        )

    def filing_index(
        self, cik: int, needed: Mapping[str, date] | None = None, *, refresh: bool = False
    ) -> pd.DataFrame:
        """Filings of `cik` with acceptance times: columns `accn, form, filed, accepted_at`.

        Reads `filings.recent`, then the history pages: all of them when `needed` is `None`,
        else only those whose date range covers the filing date of a `needed` accession
        (accession → filing date) that `recent` lacks. A filing in two tables is kept once.
        """
        subs = self.submissions(cik, refresh=refresh)
        filings = subs.get("filings")
        if not isinstance(filings, Mapping) or not isinstance(filings.get("recent"), Mapping):
            raise DataError(f"submissions of CIK {cik}: no filings.recent table")
        frames = [parse_submissions(filings["recent"])]
        have = set(frames[0]["accn"])
        wanted = (
            None
            if needed is None
            else {filed for accn, filed in needed.items() if accn not in have}
        )
        for page in filings.get("files", []):
            try:
                low = date.fromisoformat(page["filingFrom"])
                high = date.fromisoformat(page["filingTo"])
                name = page["name"]
            except (KeyError, TypeError, ValueError) as exc:
                raise DataError(f"submissions of CIK {cik}: bad history entry {page!r}") from exc
            if wanted is not None and not any(low <= d <= high for d in wanted):
                continue
            frames.append(parse_submissions(self.submissions_page(name)))
        index = pd.concat(frames, ignore_index=True)
        return index.drop_duplicates(subset="accn", keep="first").reset_index(drop=True)

    def filing_documents(self, cik: int, accn: str) -> tuple[tuple[str, str], ...]:
        """The documents of filing `accn` of `cik` as `(type, file name)` pairs, in the order of
        the filing's `-index.htm` (`EX-13`, `10-K`, ...). Not archived (Stage 29 follow-up 2)."""
        url = self._config.filing_index_url.format(cik=cik, folder=_folder(accn), accn=accn)
        page = lxml.html.fromstring(self._get_bytes(url))
        out = []
        for row in page.xpath("//table[contains(@class, 'tableFile')]//tr[td]"):
            cells = row.xpath("./td")
            links = row.xpath(".//a/@href")
            if len(cells) < 4 or not links:
                continue
            kind = cells[3].text_content().strip()
            name = str(links[0]).rsplit("/", 1)[-1]
            if kind and name:
                out.append((kind, name))
        return tuple(out)

    def filing_document(self, cik: int, accn: str, name: str) -> bytes:
        """The bytes of document `name` of filing `accn` of `cik` (HTML or text, as served).
        Not archived: the caller keeps what it needs and the document's digest."""
        if not name or "/" in name or "\\" in name or name.startswith("."):
            raise DataError(f"invalid filing document name {name!r}")
        url = self._config.filing_document_url.format(cik=cik, folder=_folder(accn), name=name)
        return self._get_bytes(url)

    # --- plumbing --------------------------------------------------------------------------------

    def _get_bytes(self, url: str) -> bytes:
        return call_with_retry(
            lambda: self._request(url),
            self._policy,
            self._sleep,
            f"GET {url}",
            (httpx.TransportError, _RetryableStatus),
            event="fundamentals.request_failed",
        )

    def _cache(self, dataset: str, filename: str) -> Path | None:
        if self._lake is None:
            return None
        return self._lake.raw_path(RAW_VENDOR, dataset) / filename

    def _get_json(self, url: str, cache: Path | None, *, refresh: bool) -> dict[str, Any]:
        if cache is not None and not refresh and cache.exists():
            return _decode(cache.read_bytes(), str(cache))
        body = self._get_bytes(url)
        data = _decode(body, url)
        if cache is not None:

            def _write(fh: BinaryIO) -> None:
                fh.write(body)

            atomic_write(cache, _write)
        return data

    def _request(self, url: str) -> bytes:
        self._throttle.wait()
        resp = self._http.get(url, headers=self._headers)
        if resp.status_code == 200:
            return resp.content
        if resp.status_code == 429 or resp.status_code >= 500:
            raise _RetryableStatus(f"HTTP {resp.status_code}")
        raise DataError(f"GET {url} returned HTTP {resp.status_code}")


def _folder(accn: str) -> str:
    """The archive folder of accession number `accn`: its digits without dashes."""
    folder = accn.replace("-", "")
    if not folder.isdigit():
        raise DataError(f"invalid accession number {accn!r}")
    return folder


def _decode(body: bytes, where: str) -> dict[str, Any]:
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise DataError(f"{where}: not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise DataError(f"{where}: expected a JSON object, got {type(data).__name__}")
    return data


def build_edgar_client(config: AtlasConfig, lake: Lake | None = None) -> EdgarClient:
    """An `EdgarClient` from the run's config: `config.data.fundamentals` and the
    `SEC_USER_AGENT` secret (`ConfigError` naming it when unset). Answers are archived in
    `lake`'s `raw/sec/` when a lake is given."""
    secret = config.secrets.sec_user_agent
    user_agent = secret.get_secret_value() if secret is not None else ""
    if not user_agent.strip():
        raise ConfigError(
            "SEC_USER_AGENT is not set: EDGAR requires a User-Agent with contact details, "
            'e.g. "Atlas Capital you@example.com"; add it to .env'
        )
    return EdgarClient(config.data.fundamentals, user_agent=user_agent, lake=lake)


def build_cik_map(
    client: EdgarClient, config: AtlasConfig | None = None, *, refresh: bool = False
) -> CikMap:
    """The ticker → CIK map for ingestion: EDGAR's `company_tickers.json` (current
    registrants) with the verified overrides of `config.data.fundamentals.cik_overrides_file`
    (former members; path relative to the working directory) applied on top."""
    cfg = config if config is not None else AtlasConfig()
    base = CikMap.from_company_tickers(client.company_tickers(refresh=refresh))
    return base.with_overrides(load_cik_overrides(cfg.data.fundamentals.cik_overrides_file))
