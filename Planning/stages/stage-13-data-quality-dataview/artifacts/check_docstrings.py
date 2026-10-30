"""List public functions, classes and methods without a docstring in the Stage 13 modules (and the new config classes)."""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
FILES = [ROOT / "src/atlas/data/quality.py", ROOT / "src/atlas/data/view.py"]
missing = []
for path in FILES:
    tree = ast.parse(path.read_text())
    if not ast.get_docstring(tree):
        missing.append(f"{path.name}: module")
    # Public = module-level, or in a class body (closures inside functions are not API).
    nodes = list(tree.body) + [
        n for c in tree.body if isinstance(c, ast.ClassDef) for n in c.body
    ]
    for node in nodes:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if node.name.startswith("_") or ast.get_docstring(node):
                continue
            missing.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.name}")
config = ast.parse((ROOT / "src/atlas/core/config.py").read_text())
for node in config.body:
    new = {"QualitySeverities", "FrequencyDays", "QualityConfig", "ViewConfig"}
    if isinstance(node, ast.ClassDef) and node.name in new and not ast.get_docstring(node):
        missing.append(f"src/atlas/core/config.py:{node.lineno} {node.name}")
print(f"checked {len(FILES)} files + 4 config classes")
print("\n".join(missing) if missing else "none missing")
sys.exit(1 if missing else 0)

