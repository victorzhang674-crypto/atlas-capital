"""List public functions, classes and methods without a docstring in the Stage 12 modules."""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
FILES = sorted((ROOT / "src/atlas/data/events").glob("*.py")) + sorted(
    (ROOT / "src/atlas/data/sentiment").glob("*.py")
)
missing = []
for path in FILES:
    tree = ast.parse(path.read_text())
    if not ast.get_docstring(tree):
        missing.append(f"{path.name}: module")
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if node.name.startswith("_") or ast.get_docstring(node):
                continue
            missing.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.name}")
print(f"checked {len(FILES)} files")
print("\n".join(missing) if missing else "none missing")
sys.exit(1 if missing else 0)
