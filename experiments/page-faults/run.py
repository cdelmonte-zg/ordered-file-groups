"""Who makes the resident memory grow: page faults by call stack, with perf.

Run from the repository root:
  python experiments/page-faults/run.py [--out DIR]

The deduplication on 1200 files with total overlap, 256 MB, both plans, one
run each under `perf record -e page-faults` with call stacks. Every page that
enters the resident set is attributed to the stack that touched it first, and
the stacks are grouped by the part of the engine they belong to. This says
who brought a page in, not who holds it at the peak.

It needs what the lab does not require elsewhere: perf, the permission to use
it (kernel.perf_event_paranoid at most 1, or root) and binaries with their
symbol table, which scripts/build_binaries.sh keeps. When one of these is
missing, the script writes the reason to results.tsv and exits with status 0:
the experiment is recorded as not run.
"""
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import output_dir, rm, sql_for, write_tsv  # noqa: E402

OUT = output_dir("page-faults")
PAGE_MB = 4096 / 2**20
# part of the engine -> what a frame of its code looks like; the first frame of a
# stack, from the innermost outward, that matches one of these decides
PARTS = [
    ("spill", re.compile(r"spill|IPCStreamWriter|arrow_ipc", re.I)),
    ("repartition", re.compile(r"repartition", re.I)),
    ("merge", re.compile(r"sorts::|streaming_merge|SortPreservingMerge", re.I)),
    ("sort", re.compile(r"ExternalSorter|sort::sort|SortExec", re.I)),
    ("aggregate", re.compile(r"aggregates::|GroupValues|group_values", re.I)),
    ("scan", re.compile(r"parquet|datasource|object_store", re.I)),
]


def why_not():
    if not shutil.which("perf"):
        return "perf is not installed"
    paranoid = int(Path("/proc/sys/kernel/perf_event_paranoid").read_text())
    if paranoid > 1 and subprocess.run(["id", "-u"], capture_output=True, text=True).stdout.strip() != "0":
        return f"kernel.perf_event_paranoid is {paranoid}; it must be at most 1"
    symbols = subprocess.run(["nm", str(rm.binary("accept-groups"))], capture_output=True, text=True)
    if symbols.returncode != 0 or "repartition" not in symbols.stdout:
        return "the binaries have no symbol table"
    return ""


def part_of(stack):
    for frame in stack:                       # innermost first
        for name, pattern in PARTS:
            if pattern.search(frame):
                return name
    return "other"


def profile(variant, sql):
    data = OUT / f"{variant}.perf.data"
    record = subprocess.run(
        ["perf", "record", "-q", "-e", "page-faults", "-c", "1", "--call-graph", "dwarf,16384",
         "-o", str(data), "--", str(rm.binary(variant)), "--memory-limit", "256m",
         "--mem-pool-type", "fair", "-f", str(sql)], capture_output=True, text=True)
    (OUT / f"{variant}.out").write_text(record.stdout)
    (OUT / f"{variant}.err").write_text(record.stderr)
    if rm.run_failed(record.returncode, record.stdout, record.stderr) or not data.is_file():
        return None
    script = subprocess.run(["perf", "script", "-i", str(data), "-F", "sym"],
                            capture_output=True, text=True).stdout
    data.unlink()                             # hundreds of MB; the counts are what is kept
    counts, stack = Counter(), []
    for line in script.splitlines() + [""]:
        if line.strip():
            stack.append(line.strip())
        elif stack:
            counts[part_of(stack)] += 1
            stack = []
    return counts


reason = why_not()
rows = []
if reason:
    rows.append({"variant": "", "part": "", "page_faults": "", "mb": "", "share_pct": "",
                 "note": f"not run: {reason}"})
    print("not run:", reason)
else:
    sql = OUT / "case.sql"
    sql.write_text(sql_for("df-16919-partial-1200-depth-1200-rank"))
    for variant in ("original", "accept-groups"):
        counts = profile(variant, sql)
        if counts is None:
            rows.append({"variant": variant, "part": "", "page_faults": "", "mb": "",
                         "share_pct": "", "note": "the run under perf failed"})
            continue
        total = sum(counts.values())
        for part, n in counts.most_common():
            rows.append({"variant": variant, "part": part, "page_faults": n,
                         "mb": round(n * PAGE_MB, 1), "share_pct": round(100 * n / total, 1),
                         "note": ""})
        print(variant, dict(counts.most_common()), flush=True)
write_tsv(OUT / "results.tsv", rows)
