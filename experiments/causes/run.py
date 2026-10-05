"""Why plans with many ordered groups fail or lose: one change at a time.

Run from the repository root:
  python experiments/causes/run.py [--out DIR]

Two observations of the lab are taken apart here.

W. The workaround (the original binary, `target_partitions` raised to the 120
   groups of the 120 files with total overlap) fails with "Resources
   exhausted". From that configuration one thing changes at a time, at 256 MB
   and at 2 GB: the pool (greedy instead of fair), the strings (plain Utf8
   instead of views), and the outputs of the repartition (the ordered plan,
   which has the same 120 ordered inputs, with the target at 2, 8, 30 and 60).

S. With about 1200 ordered streams and 128 MB the ordered plan spills in its
   final aggregate and loses to the original. At 128 MB the streams vary (150,
   300, 600, 1200 files with total overlap, both plans), and at 1200 files the
   pool and the strings change as in W.

One unrecorded warm-up per configuration, ten recorded runs, configurations
interleaved in rotating order. Writes every output and results.tsv.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import (NOVIEW, groups_by_bounds, interleaved, output_dir, sql_for,  # noqa: E402
                    timed_run, write_tsv)

OUT = output_dir("causes")
RUNS = 10


def dataset(files):
    return f"df-16919-partial-{files}-depth-{files}-rank"


configs = []


def add(part, change, files, pool, binary, target, pool_type="fair", strings="views"):
    name = f"{part}-{files}-{pool}-{binary}-t{target}-{pool_type}-{strings}"
    (OUT / f"{name}.sql").write_text(sql_for(
        dataset(files), target=target, settings=NOVIEW if strings == "utf8" else ()))
    configs.append({"name": name, "part": part, "change": change, "files": files, "pool": pool,
                    "binary": binary, "target_partitions": target, "pool_type": pool_type,
                    "strings": strings})


groups = groups_by_bounds(dataset(120))
for pool in ("256m", "2g"):
    add("W", "none", 120, pool, "original", groups)
    add("W", "pool", 120, pool, "original", groups, pool_type="greedy")
    add("W", "strings", 120, pool, "original", groups, strings="utf8")
    for outputs in (2, 8, 30, 60):
        add("W", "outputs", 120, pool, "accept-groups", outputs)
for files in (150, 300, 600, 1200):
    add("S", "none", files, "128m", "accept-groups", 2)
    add("S", "reference", files, "128m", "original", 2)
add("S", "pool", 1200, "128m", "accept-groups", 2, pool_type="greedy")
add("S", "strings", 1200, "128m", "accept-groups", 2, strings="utf8")
add("S", "pool", 1200, "128m", "original", 2, pool_type="greedy")
add("S", "strings", 1200, "128m", "original", 2, strings="utf8")


def one(cfg, tag):
    row = timed_run(cfg["binary"], OUT / f"{cfg['name']}.sql", OUT / f"{cfg['name']}-{tag}",
                    pool=cfg["pool"], pool_type=cfg["pool_type"])
    return {**{k: v for k, v in cfg.items() if k != "name"}, "run": tag, **row}


write_tsv(OUT / "results.tsv", interleaved(configs, RUNS, one))
