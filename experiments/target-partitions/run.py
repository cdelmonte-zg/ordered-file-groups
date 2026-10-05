"""Raising target_partitions to the groups needed, when the groups are many.

Run from the repository root:
  python experiments/target-partitions/run.py [--out DIR]

The matrix runs the original binary with `target_partitions` raised to the
ordered groups the files need (`original-target`) up to twelve groups. This
experiment runs it where the files need 120 and about 1200 groups, at 256 MB
and at 2 GB, beside the original plan and the ordered plan with the target
left at two. One unrecorded warm-up per configuration, ten recorded runs,
configurations interleaved in rotating order. Writes every output and
results.tsv.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import interleaved, output_dir, sql_for, timed_run, write_tsv  # noqa: E402

OUT = output_dir("target-partitions")
RUNS = 10
# files -> (dataset, ordered groups the bounds give)
CASES = {120: ("df-16919-partial-120-depth-120-rank", 120),
         1200: ("df-16919-partial-1200-depth-1200-rank", 1196)}

configs = []
for files, (dataset, groups) in CASES.items():
    for pool in ("256m", "2g"):
        for variant, binary, target in (("original", "original", 2),
                                        ("accept-groups", "accept-groups", 2),
                                        ("original-target", "original", groups)):
            name = f"{files}-{pool}-{variant}"
            (OUT / f"{name}.sql").write_text(sql_for(dataset, target=target))
            configs.append((name, files, pool, variant, binary, target))


def one(cfg, tag):
    name, files, pool, variant, binary, target = cfg
    row = timed_run(binary, OUT / f"{name}.sql", OUT / f"{name}-{tag}", pool=pool)
    return {"files": files, "pool": pool, "variant": variant, "target_partitions": target,
            "run": tag, **row}


write_tsv(OUT / "results.tsv", interleaved(configs, RUNS, one))
