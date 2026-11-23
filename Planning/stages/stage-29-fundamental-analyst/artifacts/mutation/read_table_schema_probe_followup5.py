"""Follow-up 5: are `Lake.read_table`'s dataset arguments `format="parquet"` and
`schema=schema.arrow_schema()` observable? For every registered table, write rows (values and
nulls) with `Lake.write_table`, then compare the schema pyarrow infers from the written files
(what `ds.dataset(files)` uses without `schema=`) with the declared one, and the tables read
both ways."""
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds

import atlas.cli.app  # noqa: F401  (registers every table)
from atlas.core.storage import DEFAULT_REGISTRY, Lake

same = differ = 0
with tempfile.TemporaryDirectory() as tmp:
    lake = Lake(Path(tmp))
    for name in sorted(DEFAULT_REGISTRY.names()):
        schema = lake.table_schema(name)
        arrow = schema.arrow_schema()
        rows = []
        for i in range(2):  # one row of values, one with every nullable column null
            row = {}
            for field in arrow:
                col = schema.column(field.name)
                if i == 1 and col.nullable:
                    row[field.name] = None
                    continue
                t = field.type
                if pa.types.is_timestamp(t):
                    row[field.name] = pa.scalar(1_700_000_000_000_000 + i, t).as_py()
                elif pa.types.is_date(t):
                    row[field.name] = pa.scalar(19_000 + i, pa.date32()).as_py()
                elif pa.types.is_boolean(t):
                    row[field.name] = bool(i)
                elif pa.types.is_integer(t):
                    row[field.name] = i
                elif pa.types.is_floating(t):
                    row[field.name] = 0.5 + i
                elif pa.types.is_decimal(t):
                    import decimal
                    row[field.name] = decimal.Decimal(i)
                elif pa.types.is_list(t):
                    row[field.name] = [f"v{i}"]
                else:
                    row[field.name] = f"v{i}"
            rows.append(row)
        table = pa.Table.from_pylist(rows, schema=arrow)
        try:
            frame = table.to_pandas()
            for field in arrow:  # nullable integers stay integers (Int64)
                if pa.types.is_integer(field.type):
                    frame[field.name] = frame[field.name].astype("Int64")
            lake.write_table(name, frame, mode="overwrite")
        except Exception as exc:  # a table with checks the probe's rows do not meet
            print(f"{name}: not written ({type(exc).__name__}: {str(exc)[:80]})")
            continue
        files = [str(f) for f in lake.table_files(name)]
        inferred = ds.dataset(files, format="parquet").schema
        plain = ds.dataset(files).to_table()
        declared = ds.dataset(files, format="parquet", schema=arrow).to_table()
        equal = inferred.remove_metadata().equals(arrow.remove_metadata()) and plain.equals(declared)
        same += equal
        differ += not equal
        print(f"{name}: {len(files)} file(s), inferred schema == declared: {equal}")
print(f"tables equal: {same}, different: {differ}")
