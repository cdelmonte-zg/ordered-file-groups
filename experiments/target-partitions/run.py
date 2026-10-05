"""Raising target_partitions to the groups needed, when the groups are many.

Run from the repository root:
  python experiments/target-partitions/run.py [--out DIR]

The matrix runs the original binary with `target_partitions` raised to the
ordered groups the files need (`original-target`) up to twelve groups. This
experiment runs it on the files with total overlap, 120 and 1200, at 256 MB
and at 2 GB, beside the original plan and the ordered plan with the target
left at two. A fourth configuration raises the target with
`split_file_groups_by_statistics` off, so that the scan has as many
partitions and no ordering. One unrecorded warm-up per configuration, ten recorded runs,
configurations interleaved in rotating order. Writes every output and
results.tsv.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import groups_by_bounds, interleaved, output_dir, sql_for, timed_run, write_tsv  # noqa: E402

OUT = output_dir("target-partitions")
RUNS = 10
# files -> dataset; the raised target is the ordered groups its manifest gives
CASES = {120: "df-16919-partial-120-depth-120-rank",
         1200: "df-16919-partial-1200-depth-1200-rank"}

configs = []
for files, dataset in CASES.items():
    groups = groups_by_bounds(dataset)
    for pool in ("256m", "2g"):
        for variant, binary, target, split in (("original", "original", 2, "true"),
                                               ("accept-groups", "accept-groups", 2, "true"),
                                               ("original-target", "original", groups, "true"),
                                               ("original-target-split-off", "original", groups, "false")):
            name = f"{files}-{pool}-{variant}"
            (OUT / f"{name}.sql").write_text(sql_for(dataset, target=target, split=split))
            configs.append((name, files, pool, variant, binary, target, split))


def one(cfg, tag):
    name, files, pool, variant, binary, target, split = cfg
    row = timed_run(binary, OUT / f"{name}.sql", OUT / f"{name}-{tag}", pool=pool)
    return {"files": files, "pool": pool, "variant": variant, "target_partitions": target,
            "split_by_statistics": split, "run": tag, **row}


write_tsv(OUT / "results.tsv", interleaved(configs, RUNS, one))
