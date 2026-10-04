"""Depth 1 against depth 2: the batch sizes of DESIGN.md and the layout probes.

Run from the repository root:
  python experiments/depth/run.py [--out DIR]

The deduplication query, ordered plan, 256 MB, six recorded runs per
configuration after one warm-up, configurations interleaved in rotating
order. The binary that accepts extra groups is used throughout, so that every
configuration keeps the order: with 12 files the two binaries have the same
plan, with 120 files at depth 2 the bounds give three groups.

- batch: depth 1 and depth 2 with 12 files at batch sizes 2048, 8192 (the
  default), 32768 and 131072;
- layout: depth 1 and depth 2 with 120 files, and depth 1 with 12 files split
  between the two partitions by name instead of by statistics.

Writes every output and results.tsv. A run that fails is recorded with
ok = 0 and left out of the statistics of the report.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import interleaved, output_dir, sql_for, timed_run, write_tsv  # noqa: E402

OUT = output_dir("depth")
RUNS = 6
SIZES = (2048, 8192, 32768, 131072)

# name, kind, depth, files, batch size, grouping by statistics
configs = []
for size in SIZES:
    for depth in (1, 2):
        configs.append((f"d{depth}-b{size}", "batch", depth, 12, size, "true"))
for depth in (1, 2):
    configs.append((f"d{depth}-120-files", "layout", depth, 120, 8192, "true"))
configs.append(("d1-split-by-name", "layout", 1, 12, 8192, "false"))
for name, _, depth, files, size, split in configs:
    dataset = (f"df-16919-partial-12-depth-{depth}" if files == 12
               else f"df-16919-partial-120-depth-{depth}-entity-rank")
    (OUT / f"{name}.sql").write_text(sql_for(
        dataset, split=split, settings=(("datafusion.execution.batch_size", str(size)),)))


def one(cfg, tag):
    name, kind, depth, files, size, split = cfg
    row = timed_run("accept-groups", OUT / f"{name}.sql", OUT / f"{name}-{tag}")
    return {"name": name, "kind": kind, "depth": depth, "files": files, "batch_size": size,
            "split_by_statistics": split, "run": tag, **row}


write_tsv(OUT / "results.tsv", interleaved(configs, RUNS, one))
