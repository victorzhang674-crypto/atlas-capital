"""Follow-up 5: the over-run markers (text that only follows an MD&A), shared by the scan and the comparison."""
import re, sys, pandas as pd
M = {
  "signatures": r"^[ \t]*signatures?[ \t]*$(?!\n[ \t]*\d{1,4}[ \t]*$)",
  "pursuant": r"^[ \t]*pursuant[ \t\n]+to[ \t\n]+the[ \t\n]+requirements[ \t\n]+of[ \t\n]+(?:section[ \t\n]+13|the[ \t\n]+securities[ \t\n]+exchange[ \t\n]+act)",
  "item_9a+": r"^[ \t]*(?:part[ \t]+i{1,3}v?[ \t.,\-]*)?item[ \t]*(?:\n[ \t]*)?(?:9a|9b|9c|1[0-6])(?![0-9a-z])[^\n]{0,80}$(?!\n[ \t]*\d{1,4}[ \t]*$)",
  "item_5_6_10q": r"^[ \t]*item[ \t]*(?:\n[ \t]*)?(?:5|6)(?![0-9a-z])[ \t.:\-–—]*(?:\n[ \t]*)?(?:exhibits|other[ \t]+information)[ \t.]*$(?!\n[ \t]*\d{1,4}[ \t]*$)",
  "exhibit_index": r"^[ \t]*(?:exhibit[ \t]+index|index[ \t]+to[ \t]+exhibits)[ \t]*$",
  "part_iii_iv": r"^[ \t]*part[ \t]+(?:iii|iv)[ \t.:]*$",
  "certify": r"^[ \t]*i,[ \t]+[^\n]{3,60},[ \t]+certify",
  "item_8_split": r"^[ \t]*item[ \t]*\n[ \t]*(?:7a|8)(?![0-9a-z])",
}
R = {k: re.compile(v, re.I | re.M) for k, v in M.items()}
def hits(t):
    if not isinstance(t, str): return {}
    return {k: len(rx.findall(t)) for k, rx in R.items() if rx.search(t)}
