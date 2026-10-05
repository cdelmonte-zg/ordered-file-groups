"""The base case with pools large enough for the original plan not to spill.

Run from the repository root:
  python experiments/large-pools/run.py [--out DIR]

The matrix stops at 512 MB, where the final aggregate of the original plan
still spills and that of the ordered plan does not: the gain of the ordered
plan and the spills it avoids cannot be told apart there. This experiment
runs the deduplication of the base case at 512 MB, 1, 2 and 4 GB, with the
original plan, the ordered plan and the original with `target_partitions`
raised to the groups needed, and again without the `ORDER BY` (Q5), where no
plan sorts: what the two series share does not belong to the sort. One unrecorded warm-up per configuration, ten
recorded runs, configurations interleaved in rotating order. Writes every
output and results.tsv.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import interleaved, output_dir, sql_for, timed_run, write_tsv  # noqa: E402

OUT = output_dir("large-pools")
RUNS = 10
DATASET, GROUPS = "df-16919-partial-12-depth-4", 4
# variant -> (binary, target_partitions)
VARIANTS = {"original": ("original", 2), "accept-groups": ("accept-groups", 2),
            "original-target": ("original", GROUPS)}

configs = []
for query in ("Q3", "Q5"):                # the deduplication, and the same without its ORDER BY
    for pool in ("512m", "1g", "2g", "4g"):
        for variant, (binary, target) in VARIANTS.items():
            name = f"{query}-{pool}-{variant}"
            (OUT / f"{name}.sql").write_text(sql_for(DATASET, query=query, target=target))
            configs.append((name, query, pool, variant, binary))


def one(cfg, tag):
    name, query, pool, variant, binary = cfg
    row = timed_run(binary, OUT / f"{name}.sql", OUT / f"{name}-{tag}", pool=pool)
    return {"query": query, "pool": pool, "variant": variant, "run": tag, **row}


write_tsv(OUT / "results.tsv", interleaved(configs, RUNS, one))
