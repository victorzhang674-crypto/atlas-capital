# Reused tickers: the review (Phase 2 checkpoint, Tier 4, 2026-11-14)

Every former member (not a current one) of the main lake whose price bars all start after it left the index: the scan [phase2-checkpoint-tier4-reused-tickers-scan.txt](phase2-checkpoint-tier4-reused-tickers-scan.txt). Most first bars are 2020-07-27, where Alpaca's history begins: the sync's fallback when Yahoo had nothing. Such a symbol is either the same company still trading after it left the index (kept), or a later company that took the ticker after the member was acquired, merged, failed or replaced by new equity in a bankruptcy (listed in `config/reused_tickers.yaml`: bars after `until` are not stored).

The verdicts follow what the assistant knows of each company's corporate history (acquisition and delisting dates); they are not from a data provider. Each listed case also shows in the lake as no bar during the membership and a first bar months or years after the company ceased to trade.

| Symbol | Member | First bar | Last bar | Bars | Actions | Company | Verdict |
|---|---|---|---|---:|---:|---|---|
| ADS | 2013-12-23 .. 2020-06-22 | 2020-07-27 | 2022-04-01 | 424 | 21 | Alliance Data Systems | kept: renamed Bread Financial (BFH) 2022; same company until then |
| ADT | 2012-10-01 .. 2016-05-03 | 2018-01-19 | 2026-10-30 | 2208 | 35 | ADT | **reused** (until 2016-05-02): ADT Corp taken private by Apollo 2016-05-02; ADT Inc (new company) listed 2018-01-19 |
| ALTR | 1957-03-04 .. 2015-12-29 | 2020-07-27 | 2025-03-25 | 1172 | 0 | Altera | **reused** (until 2015-12-28): Altera acquired by Intel 2015-12-28; ALTR later Altair Engineering |
| APC | 1957-03-04 .. 2019-08-09 | 2026-02-12 | 2026-10-30 | 181 | 2 | Anadarko Petroleum | **reused** (until 2019-08-08): Anadarko acquired by Occidental 2019-08-08 |
| ARNC | 2016-11-01 .. 2020-04-01 | 2020-07-27 | 2023-08-17 | 768 | 14 | Arconic | **reused** (until 2020-03-31): Arconic Inc became Howmet Aerospace (HWM) 2020-04-01; ARNC then Arconic Corp, a spin-off |
| BBBY | 1957-03-04 .. 2017-07-26 | 2026-07-17 | 2026-10-30 | 75 | 0 | Bed Bath & Beyond | **reused** (until 2023-05-02): Bed Bath & Beyond delisted in bankruptcy 2023-05 (approximate); BBBY later another listing |
| CA | 1957-03-04 .. 2018-11-06 | 2024-01-05 | 2026-05-06 | 585 | 39 | CA Technologies | **reused** (until 2018-11-05): CA Technologies acquired by Broadcom 2018-11-05 |
| CAM | 1957-03-04 .. 2016-04-04 | 2025-10-06 | 2026-10-30 | 270 | 12 | Cameron International | **reused** (until 2016-04-01): Cameron International acquired by Schlumberger 2016-04-01 |
| CHK | 1957-03-04 .. 2018-03-19 | 2021-02-10 | 2024-10-01 | 916 | 15 | Chesapeake Energy | **reused** (until 2021-02-09): old Chesapeake shares cancelled in bankruptcy; new equity from 2021-02-10 (approximate) |
| CSRA | 2015-12-01 .. 2018-04-04 | 2026-08-12 | 2026-10-30 | 57 | 1 | CSRA | **reused** (until 2018-04-03): CSRA acquired by General Dynamics 2018-04-03 |
| DNB | 1957-03-04 .. 2017-04-05 | 2020-07-27 | 2025-08-25 | 1274 | 22 | Dun & Bradstreet | **reused** (until 2019-02-08): Dun & Bradstreet taken private 2019-02-08; D&B Holdings (new company) listed 2020-07-01 |
| DNR | 1957-03-04 .. 2015-03-23 | 2020-07-27 | 2020-07-28 | 2 | 0 | Denbury Resources | kept: two bars in 2020-07, before its bankruptcy cancellation: the member's shares |
| DO | 1957-03-04 .. 2016-09-30 | 2022-03-30 | 2024-09-03 | 610 | 0 | Diamond Offshore Drilling | **reused** (until 2021-04-23): old Diamond Offshore shares cancelled in bankruptcy 2021-04 (approximate); new equity 2022 |
| EMC | 1957-03-04 .. 2016-09-08 | 2023-05-15 | 2026-10-30 | 870 | 6 | EMC Corporation | **reused** (until 2016-09-07): EMC acquired by Dell 2016-09-07 |
| ENDP | 2015-01-27 .. 2017-03-02 | 2020-07-27 | 2022-08-25 | 526 | 0 | Endo International | kept: kept trading until its 2022 bankruptcy delisting |
| FL | 2016-04-04 .. 2019-08-09 | 2020-07-27 | 2025-09-05 | 1282 | 31 | Foot Locker | kept: kept trading until acquired by Dick's in 2025 |
| INFO | 2017-06-02 .. 2022-03-02 | 2024-10-10 | 2026-10-30 | 516 | 2 | IHS Markit | **reused** (until 2022-02-28): IHS Markit merged into S&P Global 2022-02-28 |
| JWN | 1957-03-04 .. 2020-06-22 | 2020-07-27 | 2025-05-20 | 1208 | 31 | Nordstrom | kept: kept trading until taken private in 2025 |
| LEG | 1957-03-04 .. 2021-12-20 | 2026-08-26 | 2026-08-26 | 1 | 0 | Leggett & Platt | kept: still trading; one bar in the lake |
| LLL | 1957-03-04 .. 2019-07-01 | 2021-10-13 | 2022-10-06 | 191 | 14 | L3 Technologies | **reused** (until 2019-06-28): L3 Technologies merged with Harris (L3Harris, LHX) 2019-06-29 |
| LM | 1957-03-04 .. 2016-12-02 | 2020-07-27 | 2020-07-31 | 5 | 18 | Legg Mason | kept: kept trading until acquired by Franklin 2020-07-31 |
| MNK | 2014-08-18 .. 2017-07-26 | 2020-07-27 | 2023-08-28 | 262 | 0 | Mallinckrodt | kept: kept trading (bankruptcies 2020 and 2023); kept, the successor equity is uncertain |
| MON | 1957-03-04 .. 2018-06-07 | 2021-03-18 | 2022-12-23 | 197 | 10 | Monsanto | **reused** (until 2018-06-07): Monsanto acquired by Bayer 2018-06-07 |
| NE | 1957-03-04 .. 2015-07-20 | 2021-06-09 | 2026-10-30 | 1356 | 13 | Noble Corporation | **reused** (until 2021-02-04): old Noble Corp shares cancelled in bankruptcy 2021-02 (approximate); new equity 2021 |
| NFX | 2010-12-17 .. 2019-02-15 | 2026-07-07 | 2026-10-30 | 83 | 0 | Newfield Exploration | **reused** (until 2019-02-13): Newfield Exploration acquired by Encana 2019-02-13 |
| PCL | 1957-03-04 .. 2016-02-22 | 2025-08-05 | 2026-10-30 | 313 | 15 | Plum Creek Timber | **reused** (until 2016-02-19): Plum Creek merged into Weyerhaeuser 2016-02-19 |
| PDCO | 1957-03-04 .. 2018-03-19 | 2020-07-27 | 2025-04-17 | 1189 | 36 | Patterson Companies | kept: kept trading until taken private in 2025 |
| PLL | 1957-03-04 .. 2015-08-28 | 2020-07-27 | 2025-08-29 | 1269 | 0 | Pall Corporation | **reused** (until 2015-08-31): Pall acquired by Danaher 2015-08-31; PLL later Piedmont Lithium |
| POM | 1957-03-04 .. 2016-03-30 | 2025-10-08 | 2026-10-30 | 268 | 1 | Pepco Holdings | **reused** (until 2016-03-23): Pepco Holdings acquired by Exelon 2016-03-23 |
| QEP | 2010-06-30 .. 2015-07-01 | 2020-07-27 | 2021-03-16 | 161 | 3 | QEP Resources | kept: kept trading until acquired by Diamondback in 2021 |
| QRVO | 2015-06-11 .. 2024-12-23 | 2026-10-02 | 2026-10-02 | 1 | 0 | Qorvo | kept: still trading; one bar in the lake |
| SBNY | 2021-12-20 .. 2023-03-15 | 2024-08-15 | 2026-10-30 | 555 | 0 | Signature Bank | **reused** (until 2023-03-10): Signature Bank failed 2023-03-12; the later bars are a frozen feed |
| SE | 1957-03-04 .. 2017-02-28 | 2017-10-20 | 2026-10-30 | 2269 | 0 | Spectra Energy | **reused** (until 2017-02-27): Spectra Energy acquired by Enbridge 2017-02-27; SE later Sea Ltd (listed 2017-10-20) |
| SPLS | 1957-03-04 .. 2017-09-18 | 2026-01-16 | 2026-10-30 | 199 | 3 | Staples | **reused** (until 2017-09-12): Staples taken private by Sycamore 2017-09-12 |
| SRCL | 1957-03-04 .. 2018-12-03 | 2020-07-27 | 2024-11-01 | 1076 | 0 | Stericycle | kept: kept trading until acquired by WM in 2024 |
| STI | 1957-03-04 .. 2019-12-09 | 2022-05-02 | 2026-10-30 | 1130 | 1 | SunTrust Banks | **reused** (until 2019-12-06): SunTrust merged with BB&T into Truist 2019-12-06 |
| SWN | 1957-03-04 .. 2017-04-04 | 2020-07-27 | 2024-09-30 | 1049 | 0 | Southwestern Energy | kept: kept trading until merged into Expand Energy in 2024 |
| TE | 1957-03-04 .. 2016-07-01 | 2020-01-10 | 2026-10-30 | 1711 | 0 | TECO Energy | **reused** (until 2016-07-01): TECO Energy acquired by Emera 2016-07-01 |
| TGNA | 1957-03-04 .. 2017-06-02 | 2020-07-27 | 2026-03-20 | 1417 | 42 | Tegna | kept: still trading in 2026 |
| XEC | 2014-06-20 .. 2020-03-02 | 2020-07-27 | 2021-09-30 | 298 | 24 | Cimarex Energy | kept: kept trading until the Coterra merger in 2021 |
| XL | 1957-03-04 .. 2018-09-14 | 2020-12-22 | 2022-11-11 | 474 | 11 | XL Group | **reused** (until 2018-09-12): XL Group acquired by AXA 2018-09-12 |

Listed: 26; kept: 15. AVB (AvalonBay) is not in this table: its bars (2020-07-27 .. 2026-08-14, Alpaca) fall inside its membership (2007-01-10 .. 2026-08-18) and end with its 2026-08 delisting; its 2015-2020 bars are missing, the known gap for a delisted member (no free source).
