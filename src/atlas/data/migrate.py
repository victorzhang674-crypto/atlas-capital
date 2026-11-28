"""Backup before a schema migration (`atlas data migrate`).

`backup_tables` copies the folders of the tables a migration will rewrite, and the lake's
`_meta.json`, into a new folder, then checks the copy holds the same files. Restoring is the
reverse copy. Only the lake's own files are read; nothing in the lake changes.
"""

import shutil
from collections.abc import Sequence
from pathlib import Path

from atlas.core.errors import DataError
from atlas.core.storage import Lake
from atlas.core.storage.meta import META_FILENAME


def backup_tables(lake: Lake, names: Sequence[str], target: Path) -> Path:
    """Copy the tables `names` and `_meta.json` of `lake` into the new folder `target`.

    `FileExistsError` if `target` exists (a backup is never merged into another). `OSError`
    on a failed copy, and `DataError` if the copy does not hold the same Parquet files as the
    original (the partial backup is left in place for inspection)."""
    target.mkdir(parents=True)
    for name in names:
        source = lake.table_path(name)
        if source.is_dir():
            shutil.copytree(source, target / name)
        if sorted(p.relative_to(source) for p in lake.table_files(name)) != sorted(
            p.relative_to(target / name) for p in (target / name).rglob("*.parquet")
        ):
            raise DataError(f"backup of {name} does not hold the same files as the table")
    shutil.copy2(lake.curated_dir / META_FILENAME, target / META_FILENAME)
    return target
