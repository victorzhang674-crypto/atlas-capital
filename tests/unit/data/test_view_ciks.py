"""`DataView.ciks` follows the latest fact known at the cutoff, not primary-key order."""

from datetime import UTC, date, datetime

import pandas as pd
from view_helpers import make_facts

from atlas.core.storage import Lake
from atlas.data.view import DataView


def test_ciks_uses_the_latest_known_fact_not_the_sort_order(tmp_lake: Lake) -> None:
    # Tag order puts the former CIK last in the primary key. The current CIK filed later.
    facts = make_facts(
        "GOOGL",
        {
            "tag": "Assets",
            "value": 1.0,
            "end": date(2024, 12, 31),
            "filed": date(2025, 2, 1),
            "available_at": datetime(2025, 2, 1, 21, tzinfo=UTC),
            "accn": "0001652044-25-000001",
        },
        {
            "tag": "Zebra",
            "value": 1.0,
            "end": date(2015, 6, 30),
            "filed": date(2015, 8, 1),
            "unit": "shares",
            "available_at": datetime(2015, 8, 1, 21, tzinfo=UTC),
            "accn": "0001288776-15-000001",
        },
    )
    facts.loc[facts["tag"] == "Assets", "cik"] = 1652044
    facts.loc[facts["tag"] == "Zebra", "cik"] = 1288776
    tmp_lake.write_table("fundamentals_facts", facts, mode="upsert")
    current = DataView(tmp_lake, date(2025, 3, 3)).ciks(["GOOGL"])
    assert current.to_dict() == {"GOOGL": 1652044}
    former = DataView(tmp_lake, date(2015, 9, 1)).ciks(["GOOGL"])
    assert former.to_dict() == {"GOOGL": 1288776}
    assert pd.api.types.is_integer_dtype(current.dtype)
