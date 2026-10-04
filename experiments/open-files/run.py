"""The many-stream case under a limit on open files.

Run from the repository root:
  python experiments/open-files/run.py [--out DIR]

The deduplication query on 1200 files with total overlap (1196 ordered
groups), 256 MB, both binaries, with the open-file limit of the process set
to 1024, 2048, 4096, 8192 and 16384. Three runs each: the question is whether
the query completes, not how long it takes. Writes the outputs and
results.tsv. A limit above the hard limit of the machine is recorded as not
run.
"""
import resource
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import output_dir, sql_for, timed_run, write_tsv  # noqa: E402

OUT = output_dir("open-files")
LIMITS = (1024, 2048, 4096, 8192, 16384)
RUNS = 3
HARD = resource.getrlimit(resource.RLIMIT_NOFILE)[1]

sql = OUT / "case.sql"            # the same statements for both binaries
sql.write_text(sql_for("df-16919-partial-1200-depth-1200-rank"))
rows = []
for limit in LIMITS:
    for variant in ("original", "accept-groups"):
        for run in range(1, RUNS + 1):
            if HARD != resource.RLIM_INFINITY and limit > HARD:
                row = {"ok": "", "error": f"not run: the hard limit of this machine is {HARD}"}
            else:
                row = timed_run(variant, sql, OUT / f"nofile-{limit}-{variant}-{run}", nofile=limit)
            rows.append({"open_file_limit": limit, "variant": variant, "run": run,
                         "completed": row["ok"], "rss_mb": row.get("rss_mb", ""),
                         "scan_groups": row.get("scan_groups", ""), "error": row["error"]})
        print(limit, variant, [r["completed"] for r in rows[-RUNS:]], flush=True)
    write_tsv(OUT / "results.tsv", rows)
