"""String views on and off, for both plans: shapes S0 and S2, 128 and 512 MB.

Run from the repository root on the follow-up branch:
  python experiments/open-questions/string-view-both/run.py

One unrecorded warm-up per configuration, five recorded runs, configurations
interleaved. Uses the parser and the runner of scripts/run_matrix.py.
"""
import csv
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import run_matrix as rm  # noqa: E402

HERE = Path(__file__).resolve().parent
NOVIEW = ("SET datafusion.execution.parquet.schema_force_view_types = false;\n"
          "SET datafusion.sql_parser.map_string_types_to_utf8view = false;\n")
RUNS = 5
CASES = {c.name: c for c in rm.MATRIX}

configs = []
for shape in ("S0", "S2"):
    for pool in ("128m", "512m"):
        for variant in ("original", "accept-groups"):
            src = rm.sql_for(CASES[f"A4-{shape}"], variant)
            for views in ("views", "utf8"):
                sql = src if views == "views" else src.replace(
                    "SET datafusion.execution.split_file_groups_by_statistics = true;\n",
                    "SET datafusion.execution.split_file_groups_by_statistics = true;\n" + NOVIEW)
                name = f"{shape}-{pool}-{variant}-{views}"
                path = HERE / "runs" / f"{name}.sql"
                path.parent.mkdir(exist_ok=True)
                path.write_text(sql)
                configs.append((name, shape, pool, variant, views, path))

for name, shape, pool, variant, views, path in configs:
    rm.run(rm.BIN / f"datafusion-cli-{variant}-release", path, HERE / "runs" / f"{name}-warmup.out",
           HERE / "runs" / f"{name}-warmup.err", pool, 120)
rows = []
for i in range(1, RUNS + 1):
    for name, shape, pool, variant, views, path in configs:
        r = rm.run(rm.BIN / f"datafusion-cli-{variant}-release", path, HERE / "runs" / f"{name}-{i}.out",
                   HERE / "runs" / f"{name}-{i}.err", pool, 120)
        rows.append({"shape": shape, "pool": pool, "variant": variant, "strings": views, "run": i, **r})

cols = ["shape", "pool", "variant", "strings", "run", "result", "elapsed_seconds", "max_rss_mb",
        "sort_spills", "final_agg_spills", "final_agg_spill_mb", "repartition_spills",
        "partial_agg_out_mb", "repartition_out_mb", "error"]
with (HERE / "results.tsv").open("w") as f:
    w = csv.DictWriter(f, fieldnames=cols, delimiter="\t", extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)

def med(sel, k):
    v = [float(r[k]) for r in sel if r[k] != ""]
    return statistics.median(v) if v else float("nan")

lines = ["shape pool  variant        strings  ok   median_s  rss_mb  sort  final  repart  repart_out_mb"]
for name, shape, pool, variant, views, path in configs:
    sel = [r for r in rows if (r["shape"], r["pool"], r["variant"], r["strings"]) == (shape, pool, variant, views)]
    ok = [r for r in sel if r["result"] == "ok"]
    lines.append(f"{shape:5} {pool:5} {variant:14} {views:7} {len(ok)}/{len(sel)}  {med(ok,'elapsed_seconds'):.3f}    "
          f"{med(ok,'max_rss_mb'):5.0f}  {med(ok,'sort_spills'):4.0f}  {med(ok,'final_agg_spills'):5.0f}  "
          f"{med(ok,'repartition_spills'):6.0f}  {med(ok,'repartition_out_mb'):8.1f}")
(HERE / "summary.txt").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
