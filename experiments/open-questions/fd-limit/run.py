"""The many-stream case under a limit on open files.

Run from the repository root:
  python experiments/open-questions/fd-limit/run.py

The deduplication query on 1200 files with total overlap (1196 ordered
groups), 256 MB, both binaries, with the open-file limit of the process set
to 1024, 4096, 8192 and 16384. One run each: the question is whether the
query completes, not how long it takes. Writes the outputs here and
results.tsv. A limit above the hard limit of the machine is recorded as not
run.
"""
import resource
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import fresh, rm, timed_run, write_tsv  # noqa: E402

RESULTS = fresh(HERE / "results.tsv", outputs=HERE)
CASE = next(c for c in rm.MATRIX if c.name == "A5-1200-depth-1200")
LIMITS = (1024, 4096, 8192, 16384)
HARD = resource.getrlimit(resource.RLIMIT_NOFILE)[1]

sql = HERE / "case.sql"       # the same statements for both binaries
sql.write_text(rm.sql_for(CASE, "original"))
assert rm.sql_for(CASE, "accept-groups") == sql.read_text()
rows = []
for limit in LIMITS:
    for variant in ("original", "accept-groups"):
        if HARD != resource.RLIM_INFINITY and limit > HARD:
            row = {"ok": "", "error": f"not run: the hard limit of this machine is {HARD}"}
        else:
            row = timed_run(variant, sql, HERE / f"nofile-{limit}-{variant}", nofile=limit)
        rows.append({"open_file_limit": limit, "variant": variant,
                     "completed": row["ok"], "rss_mb": row.get("rss_mb", ""),
                     "scan_groups": row.get("scan_groups", ""), "error": row["error"]})
        print(limit, variant, {1: "completed", 0: "failed: " + row["error"]}.get(row["ok"], row["error"]),
              flush=True)
write_tsv(RESULTS, rows)
