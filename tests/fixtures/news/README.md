# News fixtures (Stage 11)

| File | Provenance | Shape |
|---|---|---|
| `alpaca_news_p1.json`, `alpaca_news_p2.json` | **Built**, not recorded: `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY` were empty on 2026-10-29. Headlines and ids are invented; dates are 2020-03-02/03. | Alpaca `GET /v1beta1/news` answer: `{"news": [{"id", "headline", "author", "created_at", "updated_at", "summary", "content", "url", "images", "symbols", "source"}], "next_page_token"}`. Page 1 links to page 2 with `"page-2"`; page 2 ends with `null`. Every item has a `content` body (`FULL ARTICLE BODY …`) so tests can prove bodies are never stored. Item 15000002 is a 12-ticker roundup, item 15000003 carries an invalid tag (`^DJI`), item 15000005 falls outside the tests' 2020-03-02 window. |
| `yahoo_news_aapl.json` | **Recorded** 2026-10-29 from Yahoo's search API (`yf.Search("AAPL", news_count=20).news`, yfinance 1.7.0) by `Planning/stages/stage-11-news/artifacts/record_yahoo_news.py`; only the `thumbnail` image links were removed. | A list of `{"uuid", "title", "publisher", "link", "providerPublishTime", "type", "relatedTickers"}` (no summary). yfinance's `Ticker.get_news` endpoint answered HTTP 404 that day, so the adapter uses the search API. |

Re-record Alpaca pages with real keys by calling the endpoint above with the parameters the
adapter sends (`include_content=false` keeps bodies out of real recordings).
