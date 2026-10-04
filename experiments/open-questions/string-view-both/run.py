"""String views on and off, for both plans: shapes S0 and S2, 128 and 512 MB.

Run from the repository root:
  python experiments/open-questions/string-view-both/run.py

One unrecorded warm-up per configuration, five recorded runs, configurations
interleaved in rotating order. Uses the parser and the runner of
scripts/run_matrix.py. Writes the outputs to runs/, results.tsv and summary.txt.
"""
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import fresh, interleaved, rm, write_tsv  # noqa: E402

NOVIEW = ("SET datafusion.execution.parquet.schema_force_view_types = false;\n"
          "SET datafusion.sql_parser.map_string_types_to_utf8view = false;\n")
SPLIT = "SET datafusion.execution.split_file_groups_by_statistics = true;\n"
RUNS = 5
OUT = HERE / "runs"
OUT.mkdir(exist_ok=True)
RESULTS = fresh(HERE / "results.tsv", outputs=OUT)
CASES = {c.name: c for c in rm.MATRIX}
COLUMNS = ["shape", "pool", "variant", "strings", "run", "result", "elapsed_seconds", "max_rss_mb",
           "sort_spills", "final_agg_spills", "final_agg_spill_mb", "repartition_spills",
           "partial_agg_out_mb", "repartition_out_mb", "error"]

configs = []
for shape in ("S0", "S2"):
    for pool in ("128m", "512m"):
        for variant in ("original", "accept-groups"):
            src = rm.sql_for(CASES[f"A4-{shape}"], variant)
            assert SPLIT in src
            for views in ("views", "utf8"):
                name = f"{shape}-{pool}-{variant}-{views}"
                (OUT / f"{name}.sql").write_text(
                    src if views == "views" else src.replace(SPLIT, SPLIT + NOVIEW))
                configs.append((name, shape, pool, variant, views))


def one(cfg, tag):
    name, shape, pool, variant, views = cfg
    r = rm.run(rm.binary_of(variant), OUT / f"{name}.sql", OUT / f"{name}-{tag}.out",
               OUT / f"{name}-{tag}.err", pool, 120)
    return {"shape": shape, "pool": pool, "variant": variant, "strings": views, "run": tag, **r}


rows = [{k: r.get(k, "") for k in COLUMNS} for r in interleaved(configs, RUNS, one)]
write_tsv(RESULTS, rows)


def med(sel, k):
    v = [float(r[k]) for r in sel if r[k] != ""]
    return statistics.median(v) if v else float("nan")


lines = ["shape pool  variant        strings  ok   median_s  rss_mb  sort  final  repart  repart_out_mb"]
for name, shape, pool, variant, views in configs:
    sel = [r for r in rows if (r["shape"], r["pool"], r["variant"], r["strings"]) == (shape, pool, variant, views)]
    ok = [r for r in sel if r["result"] == "ok"]
    lines.append(f"{shape:5} {pool:5} {variant:14} {views:7} {len(ok)}/{len(sel)}  {med(ok,'elapsed_seconds'):.3f}    "
                 f"{med(ok,'max_rss_mb'):5.0f}  {med(ok,'sort_spills'):4.0f}  {med(ok,'final_agg_spills'):5.0f}  "
                 f"{med(ok,'repartition_spills'):6.0f}  {med(ok,'repartition_out_mb'):8.1f}")
(HERE / "summary.txt").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
