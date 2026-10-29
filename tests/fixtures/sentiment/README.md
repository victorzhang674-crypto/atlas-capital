# Sentiment fixtures (Stage 12)

Used by `tests/unit/data/sentiment/`; no test touches the network.

| Path | What | Source |
|---|---|---|
| `lm_master_sample.csv` | 41 rows (every column) of the Loughran-McDonald Master Dictionary 1993-2025: the words the tests score, a word in two lists (VOLATILITY), neutral words, and two words removed from the positive list in 2020 (BENEFICIAL, BENEFIT) | **Excerpt** of `Loughran-McDonald_MasterDictionary_1993-2025.csv`, downloaded 2026-10-29 from the link on https://sraf.nd.edu/loughranmcdonald-master-dictionary/ |
| `stocktwits_aapl_p1.json`, `stocktwits_aapl_p2.json` | Two pages of the StockTwits symbol stream (`messages`, `cursor`) | **Built** in the documented shape of `GET /api/2/streams/symbol/{symbol}.json`; no real answer recorded (feature off by default) |
| `reddit_wsb_aapl.json` | A Reddit search listing (`data.children[].data`), incl. one malformed item | **Built** in the documented shape of `GET /r/{sub}/search.json`; no real answer recorded |

The Loughran-McDonald lists are free for academic research only (commercial licences from the
authors); the full dictionary is downloaded into the gitignored lake by `download_lm_lexicon`,
never committed. Only this short excerpt is kept for tests.
