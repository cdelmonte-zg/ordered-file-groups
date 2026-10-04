"""The many-stream case under a limit on open files.

Run from the repository root:
  python experiments/open-questions/fd-limit/run.py

The deduplication query on 1200 files with total overlap (1196 ordered
groups), 256 MB, both binaries, with the open-file limit of the process set
to 1024, 4096, 8192 and 16384. One run each: the question is whether the query
completes, not how long it takes. Writes the outputs here and results.tsv.
"""
import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import rm, timed_run  # noqa: E402

CASE = next(c for c in rm.MATRIX if c.name == "A5-1200-depth-1200")
LIMITS = (1024, 4096, 8192, 16384)
rows = []
for limit in LIMITS:
    for variant in ("original", "accept-groups"):
        sql = HERE / f"{variant}.sql"
        sql.write_text(rm.sql_for(CASE, variant))
        row = timed_run(variant, sql, HERE / f"nofile-{limit}-{variant}", nofile=limit)
        rows.append({"open_file_limit": limit, "variant": variant, **row})
        print(limit, variant, "completed" if row["ok"] else "failed: " + row["error"], flush=True)
with (HERE / "results.tsv").open("w") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter="\t")
    w.writeheader()
    w.writerows(rows)
