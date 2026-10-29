"""List public modules/classes/functions/methods without a docstring in the Stage 11 code (atlas.data.news)."""

import ast
import sys
from pathlib import Path

FILES = sorted(Path("src/atlas/data/news").rglob("*.py"))
checked, missing = 0, []
for path in FILES:
    tree = ast.parse(path.read_text())
    checked += 1
    if ast.get_docstring(tree) is None:
        missing.append(f"{path}: module")
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and not node.name.startswith("_"):
            checked += 1
            if ast.get_docstring(node) is None:
                missing.append(f"{path}:{node.lineno}: {node.name}")
print(f"{len(FILES)} modules, {checked} public modules/classes/functions/methods checked")
print("missing:", missing or "none")
sys.exit(1 if missing else 0)
