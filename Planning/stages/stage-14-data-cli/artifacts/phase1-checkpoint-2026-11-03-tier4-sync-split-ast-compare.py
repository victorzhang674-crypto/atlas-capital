"""Prove the sync split moved code without changing it: every top-level definition of the old
sync.py (functions, classes, assignments, by name) has an AST-identical twin in exactly one new
module (`_log` / `_DAY`, defined per module, may repeat); nothing else is defined."""
import ast, sys
from pathlib import Path
def defs(path):
    out = {}
    for node in ast.parse(Path(path).read_text()).body:
        if isinstance(node, (ast.Import, ast.ImportFrom)) or (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)):
            continue  # imports and docstrings
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            name = node.name
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            t = node.targets[0] if isinstance(node, ast.Assign) else node.target
            name = t.id
        else:
            name = f"<{type(node).__name__}@{node.lineno}>"
        out.setdefault(name, []).append(ast.dump(node))
    return out
old = defs(sys.argv[1])
new = {}
for f in sorted(Path(sys.argv[2]).glob("*.py")):
    if f.name == "__init__.py":
        continue
    for k, v in defs(f).items():
        new.setdefault(k, []).extend(v)
problems = []
for k, v in old.items():
    if k == "__all__":
        continue
    got = new.get(k, [])
    if not got:
        problems.append(f"missing {k}")
    elif any(g != v[0] for g in got):
        problems.append(f"changed {k}")
    elif len(got) > 1 and k not in ("_log", "_DAY"):
        problems.append(f"duplicated {k}")
problems += [f"new {k}" for k in new if k not in old]
print(f"old definitions: {len(old) - 1}; problems: {problems or 'none'}")
