import re, sys, collections
from pathlib import Path
text = Path(sys.argv[1]).read_text()
blocks = [b for b in text.split("### ")[1:]]
STR = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
def norm(s): return STR.sub('"S"', s)
cats = collections.defaultdict(list)
for b in blocks:
    name, *lines = b.strip().splitlines()
    minus = [l[1:] for l in lines if l.startswith("-")]
    plus = [l[1:] for l in lines if l.startswith("+")]
    m, p = "\n".join(minus), "\n".join(plus)
    if norm(m) == norm(p):
        cat = "string-literal-only"
    elif norm(p).replace("None", '"S"') == norm(m) or re.sub(r',\s*\)', ')', norm(m)).replace('"S"','') == re.sub(r',\s*\)', ')', norm(p)).replace('"S"','') and '"S"' in norm(m):
        cat = "string-arg-dropped-or-None"
    elif "raise " in m or "_log." in m or "errstate" in m or "warning(" in m:
        cat = "error-message/log/errstate"
    else:
        cat = "logic"
    cats[cat].append((name, m, p))
for k, v in cats.items(): print(k, len(v))
mod = collections.Counter(n.split(".")[3] for n, *_ in cats["logic"])
print("logic by module", dict(mod))
with open(sys.argv[2], "w") as fh:
    for n, m, p in cats["logic"]:
        fh.write(f"### {n}\n- {m}\n+ {p}\n\n")
