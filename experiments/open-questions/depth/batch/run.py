"""E3 of ../PLAN.md: depth 1 against depth 2 at four batch sizes.

Run from the repository root:
  python experiments/open-questions/depth/batch/run.py

Ordered plan, 256 MB, six recorded runs per cell after one warm-up, cells
interleaved. Writes every output here, results.tsv with one row per run and
summary.txt. A run that fails is recorded with ok = 0 and left out of the
means, which are given with the number of completed runs.
"""
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from common import fresh, interleaved, mean_sd, rm, timed_run, write_tsv  # noqa: E402

RESULTS = fresh(HERE / "results.tsv", outputs=HERE)
RUNS = 6
SIZES = (2048, 8192, 32768, 131072)
SPLIT = "SET datafusion.execution.split_file_groups_by_statistics = true;\n"

cells = []
for size in SIZES:
    for depth in (1, 2):
        name = f"d{depth}-b{size}"
        sql = rm.SQL.format(target=2, split="true", explain="EXPLAIN ANALYZE",
                            location=f"/tmp/df-16919-partial-12-depth-{depth}/",
                            query=rm.QUERIES["Q3"])
        assert SPLIT in sql
        sql = sql.replace(SPLIT, SPLIT + f"SET datafusion.execution.batch_size = {size};\n")
        (HERE / f"{name}.sql").write_text(sql)
        cells.append((name, depth, size))


def one(cell, tag):
    name, depth, size = cell
    row = timed_run("accept-groups", HERE / f"{name}.sql", HERE / f"{name}-{tag}")
    return {"cell": name, "depth": depth, "batch_size": size, "run": tag, **row}


rows = interleaved(cells, RUNS, one)
write_tsv(RESULTS, rows)

lines = ["batch_size\tdepth\tcompleted\twall_s mean (sd)\tcpu_s mean (sd)\tcores mean (sd)"]
gaps = []
for size in SIZES:
    cores = {}
    for depth in (1, 2):
        sel = [r for r in rows if r["batch_size"] == size and r["depth"] == depth]
        ok = [r for r in sel if r["ok"]]
        lines.append(f"{size}\t{depth}\t{len(ok)} of {len(sel)}\t{mean_sd(ok, 'wall_s')}\t"
                     f"{mean_sd(ok, 'cpu_s')}\t{mean_sd(ok, 'cores')}")
        cores[depth] = st.mean(float(r["cores"]) for r in ok) if ok else None
    if None not in cores.values():
        gaps.append(f"{size}\tgap in cores, depth 2 minus depth 1\t{cores[2] - cores[1]:.2f}")
failed = [f"{r['cell']} run {r['run']}: {r['error']}" for r in rows if not r["ok"]]
text = "\n".join(lines + [""] + gaps + ["", "failed runs: " + ("; ".join(failed) or "none")]) + "\n"
(HERE / "summary.txt").write_text(text)
print(text)
