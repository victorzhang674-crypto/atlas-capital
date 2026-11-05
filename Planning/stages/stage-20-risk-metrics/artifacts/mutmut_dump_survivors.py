"""Diff every surviving mutmut mutant against the original function (reads mutmut's
trampolined files directly; faster than one `mutmut show` per mutant)."""
import ast, difflib, re, sys
from pathlib import Path

root = Path(sys.argv[1]) / "mutants" / "src"
results = Path(sys.argv[2]).read_text().splitlines()
surv = [l.split(":")[0].strip() for l in results if l.strip().endswith("survived")]
funcs = {}
for path in (root / "atlas/quant/metrics").glob("*.py"):
    src = path.read_text()
    mod = "atlas.quant.metrics." + path.stem
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and "__mutmut_" in node.name:
            funcs[(mod, node.name)] = ast.get_source_segment(src, node)
out = []
for name in surv:
    mod, _, fn = name.rpartition(".")
    base = fn.rsplit("__mutmut_", 1)[0]
    orig, mut = funcs.get((mod, base + "__mutmut_orig")), funcs.get((mod, fn))
    if orig is None or mut is None:
        out.append(f"### {name}\n(not found)\n"); continue
    orig = orig.replace(base + "__mutmut_orig", base)
    mut = mut.replace(fn, base)
    diff = [l for l in difflib.unified_diff(orig.splitlines(), mut.splitlines(), lineterm="", n=0)
            if not l.startswith(("---", "+++", "@@"))]
    out.append(f"### {name}\n" + "\n".join(diff) + "\n")
Path(sys.argv[3]).write_text("\n".join(out))
print(len(surv), "survivors dumped")
