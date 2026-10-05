"""More rows for the same files, in two ways: more keys under a prefix, more prefixes.

Run from the repository root:
  python experiments/rows/run.py [--out DIR]

Every other query of the lab reads 600,000 rows. Here the base layout (twelve
files, depth 4) is generated at 6 and 24 million rows in two series. In one
the distinct (col_1, col_2) prefixes stay those of the base case and the
grouping keys under each prefix grow with the rows; in the other the prefixes
grow with the rows and the keys under each stay about as in the base case.
A last dataset concentrates the keys of 6 million rows under few prefixes.
The manifests give both numbers for every dataset. The deduplication runs
under two pools: 256 MB, and a pool chosen for each size so that the original
plan has room. The plans are the original, the ordered one and the original
with `target_partitions` raised to the groups needed. One unrecorded warm-up
per configuration, five recorded runs, configurations interleaved in rotating
order. Writes every output and results.tsv. What the plans reserve is in the
experiment with the traced binaries, on the same datasets.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import (ROWS_DATASETS, ROWS_SMALL_POOL, groups_by_bounds, interleaved,  # noqa: E402
                    manifest_value, output_dir, sql_for, timed_run, write_tsv)

OUT = output_dir("rows")
RUNS = 5
SIZES, SMALL = ROWS_DATASETS, ROWS_SMALL_POOL

configs = []
for series, rows, dataset, large in SIZES:
    groups = groups_by_bounds(dataset)
    shape = {"distinct_prefixes": manifest_value(dataset, "distinct_prefixes"),
             "keys_per_prefix": manifest_value(dataset, "grouping_keys_per_prefix"),
             "keys_per_prefix_max": manifest_value(dataset, "grouping_keys_per_prefix_max")}
    for kind, pool in (("small", SMALL), ("large", large)):
        for variant, binary, target in (("original", "original", 2),
                                        ("accept-groups", "accept-groups", 2),
                                        ("original-target", "original", groups)):
            name = f"{series}-{rows}-{pool}-{variant}"
            (OUT / f"{name}.sql").write_text(sql_for(dataset, target=target))
            configs.append((name, series, rows, shape, kind, pool, variant, binary, target))


def one(cfg, tag):
    name, series, rows, shape, kind, pool, variant, binary, target = cfg
    row = timed_run(binary, OUT / f"{name}.sql", OUT / f"{name}-{tag}", pool=pool, timeout=900)
    return {"series": series, "rows": rows, **shape, "pool_kind": kind, "pool": pool,
            "variant": variant, "target_partitions": target, "run": tag, **row}


write_tsv(OUT / "results.tsv", interleaved(configs, RUNS, one))
