import html, re, time, httpx
UA = "AtlasCapital/0.1 (research and paper-trading simulator)"
def get(url, **p):
    for w in (0.3, 2, 5):
        time.sleep(w)
        try:
            r = httpx.get(url, params=p, headers={"User-Agent": UA}, timeout=60)
        except httpx.TransportError:
            continue
        if r.status_code < 500: return r
    return r
def text(url): return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", get(url).text)))
t = text("https://www.sec.gov/Archives/edgar/data/1336917/000133691716000116/" + get("https://efts.sec.gov/LATEST/search-index", q='"UAA"', dateRange="custom", startdt="2016-11-27", enddt="2016-11-29", forms="8-K").json()["hits"]["hits"][0]["_id"].split(":",1)[1])
i = t.find("will become effective at the start of trading"); print("UA:", t[i:i+200])
def search(q, start, end, ticker, forms=None):
    p = {"q": q, "dateRange": "custom", "startdt": start, "enddt": end}
    if forms: p["forms"] = forms
    hits = get("https://efts.sec.gov/LATEST/search-index", **p).json()["hits"]["hits"]
    print(f"\n### {q} {start}..{end} {forms}: {len(hits)} hits")
    for hit in hits[:8]:
        s = hit["_source"]; adsh, doc = hit["_id"].split(":", 1)
        tt = text(f"https://www.sec.gov/Archives/edgar/data/{s['ciks'][0].lstrip('0')}/{adsh.replace('-', '')}/{doc}")
        for m in re.finditer(rf"(?<![A-Za-z]){ticker}(?![A-Za-z])", tt):
            w = tt[max(0, m.start()-260): m.end()+160]
            if re.search(r"ticker|symbol|trad", w, re.I):
                print(f"  {s['form']} {s['file_date']} {adsh} {(s.get('display_names') or ['?'])[0]}\n    > ...{w}..."); break
search('Anthem', "2014-11-25", "2014-12-15", "ANTM", "8-K")
search('"ANTM"', "2014-12-01", "2015-03-31", "ANTM")
search('Andeavor', "2017-07-25", "2017-08-15", "ANDV", "8-K")
search('"ANDV"', "2017-08-01", "2017-11-30", "ANDV")
