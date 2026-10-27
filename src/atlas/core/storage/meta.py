"""`curated/_meta.json`: the stored schema version, partitioning and fingerprint per table.

The file is small JSON, rewritten whole and atomically (temp file + `os.replace`) on every
change, so a reader never sees a half-written file:

    {"format": 1, "tables": {"prices_daily": {"version": 1, "partition_by": ["symbol"],
                                              "fingerprint": "<sha256 hex>"}}}
"""

import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from atlas.core.errors import DataError

META_FILENAME = "_meta.json"
META_FORMAT = 1
"""Version of the `_meta.json` layout itself (not of any table)."""


@dataclass(frozen=True)
class TableMeta:
    """What the lake recorded for one table: the schema `version` its files follow, the
    `partition_by` they are laid out with, and the schema `fingerprint` at that version."""

    version: int
    partition_by: tuple[str, ...]
    fingerprint: str


def atomic_write(path: Path, write: Callable[[BinaryIO], None]) -> None:
    """Write `path` via a hidden temp file in the same directory, fsync it, then move it into
    place with `os.replace` (atomic on POSIX). The temp file is removed if `write` fails."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            write(fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


class MetaStore:
    """Reads and rewrites one `_meta.json` file. A missing file means no table was written."""

    def __init__(self, path: Path) -> None:
        """A store backed by `path` (created on the first `put`)."""
        self._path = path

    @property
    def path(self) -> Path:
        """The JSON file."""
        return self._path

    def tables(self) -> dict[str, TableMeta]:
        """Every recorded table. Raises `DataError` if the file is unreadable or malformed."""
        if not self._path.exists():
            return {}
        try:
            body = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(body, dict) or body.get("format") != META_FORMAT:
                raise ValueError(f"expected an object with format {META_FORMAT}")
            return {name: _parse_entry(entry) for name, entry in body["tables"].items()}
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise DataError(f"{self._path} is not a valid lake meta file: {exc}") from exc

    def get(self, name: str) -> TableMeta | None:
        """The record for table `name`, or `None` if it was never written."""
        return self.tables().get(name)

    def put(self, name: str, meta: TableMeta) -> None:
        """Record `meta` for table `name`, rewriting the file atomically."""
        tables = self.tables()
        tables[name] = meta
        body = {
            "format": META_FORMAT,
            "tables": {
                key: {
                    "version": value.version,
                    "partition_by": list(value.partition_by),
                    "fingerprint": value.fingerprint,
                }
                for key, value in sorted(tables.items())
            },
        }
        data = (json.dumps(body, indent=2, sort_keys=True) + "\n").encode("utf-8")

        def _write(fh: BinaryIO) -> None:
            fh.write(data)

        atomic_write(self._path, _write)


def _parse_entry(entry: object) -> TableMeta:
    if not isinstance(entry, dict):
        raise TypeError("a table entry must be an object")
    version = entry["version"]
    partition_by = entry["partition_by"]
    fingerprint = entry["fingerprint"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError(f"bad version {version!r}")
    if not isinstance(partition_by, list) or not all(isinstance(p, str) for p in partition_by):
        raise ValueError(f"bad partition_by {partition_by!r}")
    if not isinstance(fingerprint, str):
        raise ValueError(f"bad fingerprint {fingerprint!r}")
    return TableMeta(version=version, partition_by=tuple(partition_by), fingerprint=fingerprint)
