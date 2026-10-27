import inspect, importlib
mods = ["atlas.core.storage", "atlas.core.storage.schema", "atlas.core.storage.meta", "atlas.core.storage.lake", "atlas.core.storage.db"]
checked = 0; missing = []
for name in mods:
    m = importlib.import_module(name)
    if not (m.__doc__ or "").strip(): missing.append(name)
    for attr, obj in vars(m).items():
        if attr.startswith("_") or getattr(obj, "__module__", None) != name: continue
        if not (inspect.isclass(obj) or inspect.isfunction(obj)): continue
        checked += 1
        if not (obj.__doc__ or "").strip(): missing.append(f"{name}.{attr}")
        if inspect.isclass(obj):
            for meth, fn in vars(obj).items():
                if meth.startswith("_") : continue
                if inspect.isfunction(fn) or isinstance(fn, property):
                    checked += 1
                    doc = (fn.fget.__doc__ if isinstance(fn, property) else fn.__doc__) or ""
                    if not doc.strip(): missing.append(f"{name}.{attr}.{meth}")
print(f"modules: {len(mods)}; public classes/functions/methods/properties checked: {checked}")
print("missing docstrings:", missing or "none")
