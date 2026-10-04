"""String views on and off, for both plans: shapes S0 and S2, 128 and 512 MB.

Run from the repository root:
  python experiments/string-views/run.py [--out DIR]

The deduplication query of the matrix on the narrow and on the wide strings,
read as Arrow string views (the default) and as plain Utf8, which needs two
settings: the Parquet reader's and the SQL planner's. One unrecorded warm-up
per configuration, five recorded runs, configurations interleaved in rotating
order. Writes every output and results.tsv.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import NOVIEW, interleaved, output_dir, sql_for, timed_run, write_tsv  # noqa: E402

OUT = output_dir("string-views")
RUNS = 5

configs = []
for shape in ("S0", "S2"):
    for pool in ("128m", "512m"):
        for variant in ("original", "accept-groups"):
            for strings in ("views", "utf8"):
                name = f"{shape}-{pool}-{variant}-{strings}"
                (OUT / f"{name}.sql").write_text(sql_for(
                    f"df-16919-partial-12-depth-4-{shape}",
                    settings=NOVIEW if strings == "utf8" else ()))
                configs.append((name, shape, pool, variant, strings))


def one(cfg, tag):
    name, shape, pool, variant, strings = cfg
    row = timed_run(variant, OUT / f"{name}.sql", OUT / f"{name}-{tag}", pool=pool)
    return {"shape": shape, "pool": pool, "variant": variant, "strings": strings, "run": tag, **row}


write_tsv(OUT / "results.tsv", interleaved(configs, RUNS, one))
