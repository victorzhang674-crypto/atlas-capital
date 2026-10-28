"""Mutation checks for Stage 09 tests that were never red (written after their code).

Each mutation edits one line of source, runs the named tests (expecting a failure = the
mutant is killed), then restores the file byte for byte. Run from the repo root:
uv run python Planning/stages/stage-09-fundamentals/artifacts/mutation_checks.py
"""

import subprocess
import sys
from pathlib import Path

F = "src/atlas/data/fundamentals/"
T = "tests/unit/data/fundamentals/"
MUTANTS = [
    ("config: SEC rate cap le=10 -> le=20", "src/atlas/core/config.py",
     "max_requests_per_second: Annotated[float, Field(gt=0, le=10)]",
     "max_requests_per_second: Annotated[float, Field(gt=0, le=20)]",
     "tests/unit/core/test_config.py::test_fundamentals_validation"),
    ("config: quarter/annual overlap check removed", "src/atlas/core/config.py",
     "if self.quarter_days[1] >= self.annual_days[0]:", "if False:",
     "tests/unit/core/test_config.py::test_fundamentals_validation"),
    ("config: default staleness 400 -> 365", "src/atlas/core/config.py",
     "max_staleness_days: Annotated[int, Field(gt=0)] = 400",
     "max_staleness_days: Annotated[int, Field(gt=0)] = 365",
     "tests/unit/core/test_config.py::test_fundamentals_defaults"),
    ("client: page-name check removed", F + "edgar_client.py",
     'if "/" in name or "\\\\" in name or not name.endswith(".json"):', "if False:",
     T + "test_fundamentals_guards.py::test_submissions_page_rejects_bad_names"),
    ("client: closes an injected pool", F + "edgar_client.py",
     "if self._owns_http:", "if True:",
     T + "test_fundamentals_guards.py::test_client_keeps_an_injected_pool_open"),
    ("client: JSON array accepted", F + "edgar_client.py",
     "if not isinstance(data, dict):", "if False:",
     T + "test_fundamentals_guards.py::test_client_rejects_a_json_array"),
    ("parser: non-finite values accepted", F + "parser.py",
     "or not math.isfinite(value):", ":",
     T + "test_fundamentals_guards.py::test_parse_companyfacts_guards"),
    ("parser: string fy accepted", F + "parser.py",
     "if fy is not None and (isinstance(fy, bool) or not isinstance(fy, int)):", "if False:",
     T + "test_fundamentals_guards.py::test_parse_companyfacts_guards"),
    ("mapping: repeated tag accepted", F + "mapping.py",
     "if len(set(terms)) != len(terms):", "if False:",
     T + "test_fundamentals_guards.py::test_parse_alternative_rejects_a_repeated_tag"),
    ("mapping: empty sum_of part accepted", F + "mapping.py",
     "if value is not None and any(not part for part in value):", "if False:",
     T + "test_fundamentals_guards.py::test_concept_map_rejects_an_empty_sum_part"),
    ("pit: staleness boundary < -> <=", F + "pit.py",
     "if found is not None and found.period_end < floor:",
     "if found is not None and found.period_end <= floor + timedelta(days=1):",
     T + "test_fundamentals_pit.py::test_stale_metric_is_nan"),
    ("pit: look-ahead guard removed", F + "pit.py",
     "    assert_no_lookahead(known, as_of)\n", "",
     T + "test_fundamentals_pit.py::test_guard_raises_if_filter_bypassed"),
]

killed = 0
for name, path, old, new, test in MUTANTS:
    file = Path(path)
    original = file.read_bytes()
    text = original.decode()
    if text.count(old) != 1:
        print(f"SKIPPED (pattern not unique/absent): {name}")
        continue
    file.write_text(text.replace(old, new))
    try:
        run = subprocess.run(["uv", "run", "pytest", "-q", "-x", test], capture_output=True, text=True)
    finally:
        file.write_bytes(original)
    status = "KILLED" if run.returncode != 0 else "SURVIVED"
    killed += status == "KILLED"
    last = run.stdout.strip().splitlines()[-1] if run.stdout.strip() else ""
    print(f"{status}: {name} -> {test.rsplit('::', 1)[-1]} ({last})")
print(f"{killed}/{len(MUTANTS)} killed")
sys.exit(0 if killed == len(MUTANTS) else 1)
