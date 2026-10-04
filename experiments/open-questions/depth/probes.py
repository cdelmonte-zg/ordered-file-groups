"""The first probes of the depth question: same plan, different layouts of the files.

Run from the repository root:
  python experiments/open-questions/depth/probes.py

Depth 1 and depth 2 with 12 and with 120 files, and depth 1 with the files
split between the two partitions by name instead of by statistics. 256 MB,
six recorded runs each after a warm-up, interleaved. The original binary is
used: at these depths it keeps the order by itself, except in the split by
name, where the grouping by statistics is off. Writes the outputs to
probes/, probes.tsv and probes-summary.txt. Needs, beside the datasets of
generate_round5.sh, the 120-file datasets of depth 1 and 2:
  python scripts/generate_partial_overlap.py --files 120 --depth 1 --assign entity-rank
  python scripts/generate_partial_overlap.py --files 120 --depth 2 --assign entity-rank
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import fresh, interleaved, mean_sd, timed_run, write_tsv  # noqa: E402

RESULTS = fresh(HERE / "probes.tsv")
RUNS = 6
OUT = HERE / "probes"
OUT.mkdir(exist_ok=True)
# label, SQL file
PROBES = [
    ("depth 1, 12 files", "depth-1.sql"),
    ("depth 2, 12 files", "depth-2.sql"),
    ("depth 1, 120 files", "depth-1-120.sql"),
    ("depth 2, 120 files", "depth-2-120.sql"),
    ("depth 1, 12 files, files split by name", "depth-1-byname.sql"),
]


def one(probe, tag):
    label, sql = probe
    row = timed_run("original", HERE / sql, OUT / f"{Path(sql).stem}-{tag}")
    return {"probe": label, "run": tag, **row}


rows = interleaved(PROBES, RUNS, one)
write_tsv(RESULTS, rows)
lines = ["probe\tcompleted\tscan groups\twall_s mean (sd)\tcpu_s mean (sd)\tcores mean (sd)"]
for label, _ in PROBES:
    sel = [r for r in rows if r["probe"] == label]
    ok = [r for r in sel if r["ok"]]
    groups = ", ".join(sorted({r["scan_groups"] for r in ok})) or "-"
    lines.append(f"{label}\t{len(ok)} of {len(sel)}\t{groups}\t{mean_sd(ok, 'wall_s')}\t"
                 f"{mean_sd(ok, 'cpu_s')}\t{mean_sd(ok, 'cores')}")
text = "\n".join(lines) + "\n"
(HERE / "probes-summary.txt").write_text(text)
print(text)
