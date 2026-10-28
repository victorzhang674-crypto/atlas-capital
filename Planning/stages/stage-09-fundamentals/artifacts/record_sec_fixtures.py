"""Record the Stage 09 SEC EDGAR fixtures (run once; output in record-fixtures-output.txt).

Fetches real data.sec.gov responses for AAPL, MSFT and JPM and trims them:
- companyfacts: only the tags named in config/xbrl_map.yaml (all units, all filings);
- submissions: only the filing rows whose accession number appears in the trimmed facts,
  every column kept; the paged history files (`filings.files`) are recorded when their date
  range covers the filing date of a fact whose accession is not in `recent` (the same rule
  as `EdgarClient.filing_index`), trimmed the same way.
- company_tickers.json is BUILT in the documented shape ({"0": {"cik_str", "ticker",
  "title"}}) from the real submissions `cik`/`tickers`/`name` of six registrants, because
  www.sec.gov answers 403 to a User-Agent without contact details (SEC_USER_AGENT is not set
  in this environment); data.sec.gov answers 200.

Usage: uv run python Planning/stages/stage-09-fundamentals/artifacts/record_sec_fixtures.py
"""

import json
import re
import time
from datetime import date
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "tests" / "fixtures" / "fundamentals"
UA = "AtlasCapital/0.1 (research and paper-trading simulator)"
FACT_CIKS = {"AAPL": 320193, "MSFT": 789019, "JPM": 19617}
TICKER_CIKS = [320193, 789019, 19617, 1067983, 1652044, 1326801]  # + BRK, GOOGL, META


def get(client: httpx.Client, url: str) -> dict:
    time.sleep(0.15)  # stay well under the SEC's 10 requests per second
    resp = client.get(url)
    resp.raise_for_status()
    return resp.json()


def mapped_tags() -> set[tuple[str, str]]:
    body = yaml.safe_load((ROOT / "config" / "xbrl_map.yaml").read_text())
    alternatives: list[str] = []
    for concept in body["concepts"].values():
        if "tags" in concept:
            alternatives += concept["tags"]
        for part in concept.get("sum_of", []):
            alternatives += part
    tags = set()
    for alt in alternatives:
        for term in re.split(r"\s*\+\s*", alt):
            ns, _, name = term.rpartition(":")
            tags.add((ns or "us-gaap", name))
    return tags


def dump(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), sort_keys=False) + "\n")


def trim_rows(table: dict, keep: set[str]) -> dict:
    idx = [i for i, a in enumerate(table["accessionNumber"]) if a in keep]
    return {k: [v[i] for i in idx] for k, v in table.items()}


def main() -> None:
    tags = mapped_tags()
    print(f"mapped tags: {len(tags)}")
    with httpx.Client(headers={"User-Agent": UA}, timeout=60) as client:
        for symbol, cik in FACT_CIKS.items():
            facts = get(client, f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
            kept = {
                ns: {t: v for t, v in body.items() if (ns, t) in tags}
                for ns, body in facts["facts"].items()
            }
            kept = {ns: body for ns, body in kept.items() if body}
            trimmed = {"cik": facts["cik"], "entityName": facts["entityName"], "facts": kept}
            dump(OUT / "companyfacts" / f"CIK{cik:010d}.json", trimmed)
            entries = [
                e for body in kept.values() for v in body.values() for es in v["units"].values()
                for e in es
            ]
            accns = {e["accn"] for e in entries}
            filed_of = {e["accn"]: e["filed"] for e in entries}
            print(f"{symbol}: {sum(len(b) for b in kept.values())} tags, {len(entries)} facts, "
                  f"{len(accns)} filings")

            subs = get(client, f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
            recent = subs["filings"]["recent"]
            in_recent = set(recent["accessionNumber"]) & accns
            subs["filings"]["recent"] = trim_rows(recent, accns)
            dump(OUT / "submissions" / f"CIK{cik:010d}.json", subs)
            missing_dates = {date.fromisoformat(filed_of[a]) for a in accns - in_recent}
            pages = 0
            for page in subs["filings"]["files"]:
                lo = date.fromisoformat(page["filingFrom"])
                hi = date.fromisoformat(page["filingTo"])
                if not any(lo <= d <= hi for d in missing_dates):
                    continue
                body = get(client, f"https://data.sec.gov/submissions/{page['name']}")
                dump(OUT / "submissions" / page["name"], trim_rows(body, accns))
                pages += 1
            print(f"  submissions: {len(in_recent)} in recent, {pages} history page(s) recorded")

        rows = []
        for cik in TICKER_CIKS:
            subs = get(client, f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
            rows += [(int(subs["cik"]), t, subs["name"]) for t in subs["tickers"]]
        tickers = {
            str(i): {"cik_str": c, "ticker": t, "title": n} for i, (c, t, n) in enumerate(rows)
        }
        dump(OUT / "company_tickers.json", tickers)
        print(f"company_tickers.json: {len(tickers)} tickers: {[r[1] for r in rows]}")


if __name__ == "__main__":
    main()
