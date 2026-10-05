"""What the memory pool grants and refuses, with the traced binaries.

Run from the repository root:
  python experiments/trace/run.py [--out DIR]

The traced binaries (`patch/trace-pool.patch`) wrap the memory pool and write,
for every run, what its consumers reserved and what they were refused
(`<name>-<run>.trace.tsv`; the format is in the patch). Every configuration
runs with the binary of the lab and with the traced one, interleaved: the
wrapper takes a lock on every call and can change how the tasks interleave,
so the two are compared on completed runs and spills before a trace is read.
No time of a traced run is used.

Configurations: the base case, original plan, at three pools, and the ordered
plan at one; the workaround with 120 groups at two pools, under the fair and
the greedy pool; the many-stream case at 128 MB, both plans, both pools, and the
ordered plan with 150, 300 and 600 streams; the
datasets of the experiment on rows, original and ordered plan, small and
large pool. One unrecorded warm-up per configuration, five recorded runs.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import (ROWS_DATASETS, ROWS_SMALL_POOL, groups_by_bounds, interleaved,  # noqa: E402
                    output_dir, sql_for, timed_run, write_tsv)

OUT = output_dir("trace")
RUNS = 5
BASE = ROWS_DATASETS[0][2]
W = "df-16919-partial-120-depth-120-rank"
S = "df-16919-partial-1200-depth-1200-rank"

cases = []


def add(case, dataset, binary, target, pool, pool_type="fair"):
    cases.append({"case": case, "dataset": dataset, "binary": binary, "target_partitions": target,
                  "pool": pool, "pool_type": pool_type})


for pool in ("256m", "512m", "2g"):
    add("base", BASE, "original", 2, pool)
add("base", BASE, "accept-groups", 2, "256m")
for pool in ("256m", "2g"):
    for pool_type in ("fair", "greedy"):
        add("workaround", W, "original", groups_by_bounds(W), pool, pool_type)
for binary in ("original", "accept-groups"):
    for pool_type in ("fair", "greedy"):
        add("many-streams", S, binary, 2, "128m", pool_type)
for files in (150, 300, 600):             # the ordered plan as the ordered streams grow
    add(f"streams-{files}", f"df-16919-partial-{files}-depth-{files}-rank", "accept-groups", 2, "128m")
for series, rows, dataset, large in ROWS_DATASETS:      # as in experiments/rows
    for binary in ("original", "accept-groups"):
        for pool in (ROWS_SMALL_POOL, large):
            add(f"rows-{series}-{rows}", dataset, binary, 2, pool)

configs = []
for c in cases:
    name = "-".join(str(c[k]) for k in ("case", "binary", "target_partitions", "pool", "pool_type"))
    (OUT / f"{name}.sql").write_text(sql_for(c["dataset"], target=c["target_partitions"]))
    for traced in (0, 1):
        configs.append({**c, "name": name, "traced": traced})


def one(cfg, tag):
    stem = OUT / f"{cfg['name']}-{'traced' if cfg['traced'] else 'plain'}-{tag}"
    trace = Path(f"{stem}.trace.tsv")
    row = timed_run(cfg["binary"] + ("-trace" if cfg["traced"] else ""), OUT / f"{cfg['name']}.sql",
                    stem, pool=cfg["pool"], pool_type=cfg["pool_type"], timeout=900,
                    env={"DATAFUSION_TRACE_POOL": str(trace)} if cfg["traced"] else None)
    return {**{k: v for k, v in cfg.items() if k != "name"}, "run": tag,
            "trace_file": trace.name if cfg["traced"] and trace.is_file() else "", **row}


write_tsv(OUT / "results.tsv", interleaved(configs, RUNS, one))
