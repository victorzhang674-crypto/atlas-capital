import html, re, sys, time
import httpx
UA = "AtlasCapital/0.1 (research and paper-trading simulator)"
def get(url, **p):
    for w in (0.3, 2, 5):
        time.sleep(w)
        try:
            r = httpx.get(url, params=p, headers={"User-Agent": UA}, timeout=60)
        except httpx.TransportError:
            continue
        if r.status_code < 500:
            return r
    return r
def run(q, start, end, ticker, forms=None, n=6):
    p = {"q": q, "dateRange": "custom", "startdt": start, "enddt": end}
    if forms: p["forms"] = forms
    r = get("https://efts.sec.gov/LATEST/search-index", **p)
    print(f"\n### q={q} {start}..{end} forms={forms} -> HTTP {r.status_code}")
    if r.status_code != 200: return
    for hit in r.json()["hits"]["hits"][:n]:
        s = hit["_source"]; adsh, doc = hit["_id"].split(":", 1)
        url = f"https://www.sec.gov/Archives/edgar/data/{s['ciks'][0].lstrip('0')}/{adsh.replace('-', '')}/{doc}"
        t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", get(url).text)))
        ws = []
        for m in re.finditer(rf"(?<![A-Za-z]){re.escape(ticker)}(?![A-Za-z])", t):
            w = t[max(0, m.start()-250): m.end()+150]
            if re.search(r"ticker|symbol", w, re.I): ws.append(w)
        if ws:
            print(f"  {s['form']} {s['file_date']} {adsh} {(s.get('display_names') or ['?'])[0]}")
            for w in ws[:2]: print(f"    > ...{w}...")
run('"ANTM"', "2014-11-20", "2014-12-20", "ANTM", "8-K")
run('Anthem "ANTM"', "2014-11-01", "2015-01-31", "ANTM")
run('"UAA"', "2016-11-01", "2016-12-31", "UAA", "8-K")
run('"UA.C"', "2016-03-01", "2016-04-30", "UA.C")
run('"ANDV"', "2017-07-15", "2017-08-20", "ANDV", "8-K")
run('Andeavor "ANDV"', "2017-06-01", "2017-09-30", "ANDV")
