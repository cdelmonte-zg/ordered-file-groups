"""E3 of ../PLAN.md: depth 1 against depth 2 at four batch sizes.

Run from the repository root:
  python experiments/open-questions/depth/batch/run.py

Ordered plan, 256 MB, six recorded runs per cell after one warm-up, cells
interleaved. Writes every output here, results.tsv with one row per run and
summary.txt. A run that fails is recorded with ok = 0 and left out of the
means, which are given with the number of completed runs.
"""
import csv
import re
import statistics as st
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "scripts"))
import run_matrix as rm  # noqa: E402

HERE = Path(__file__).resolve().parent
RUNS = 6
SIZES = (2048, 8192, 32768, 131072)
SPLIT = "SET datafusion.execution.split_file_groups_by_statistics = true;\n"
TIME = re.compile(r"BENCH wall=([\d.]+) user=([\d.]+) sys=([\d.]+) rss_kb=(\d+)")

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


def one(name, depth, size, tag):
    cmd = ["/usr/bin/time", "-f", "BENCH wall=%e user=%U sys=%S rss_kb=%M",
           str(rm.BIN / "datafusion-cli-accept-groups-release"), "--memory-limit", "256m",
           "--mem-pool-type", "fair", "-f", str(HERE / f"{name}.sql")]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    (HERE / f"{name}-{tag}.out").write_text(res.stdout)
    (HERE / f"{name}-{tag}.err").write_text(res.stderr)
    tm = TIME.search(res.stderr)
    failed = (res.returncode != 0 or "Plan with Metrics" not in res.stdout
              or re.search(r"Resources exhausted|\*\*Error\*\*|^Error:|^IO error", res.stderr, re.M))
    errors = [l for l in res.stderr.splitlines() if l.strip() and not l.startswith("BENCH ")]
    wall, user, sys_ = (float(tm.group(i)) for i in (1, 2, 3)) if tm else (0.0, 0.0, 0.0)
    return {"cell": name, "depth": depth, "batch_size": size, "run": tag, "ok": int(not failed),
            "wall_s": wall, "cpu_s": round(user + sys_, 2),
            "cores": round((user + sys_) / wall, 3) if wall else "",
            "rss_mb": round(int(tm.group(4)) / 1024) if tm else "",
            "error": errors[0][:160] if failed and errors else ""}


for cell in cells:
    one(*cell, "warmup")
rows = []
for i in range(1, RUNS + 1):
    for cell in cells:
        rows.append(one(*cell, str(i)))
    print(f"round {i} done", flush=True)
with (HERE / "results.tsv").open("w") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter="\t")
    w.writeheader()
    w.writerows(rows)


def stats(sel, key):
    v = [float(r[key]) for r in sel]
    if not v:
        return "no completed run"
    return f"{st.mean(v):.3f} (sd {st.stdev(v) if len(v) > 1 else 0:.3f})"


lines = ["batch_size\tdepth\tcompleted\twall_s mean (sd)\tcpu_s mean (sd)\tcores mean (sd)"]
gaps = []
for size in SIZES:
    cores = {}
    for depth in (1, 2):
        sel = [r for r in rows if r["batch_size"] == size and r["depth"] == depth]
        ok = [r for r in sel if r["ok"]]
        lines.append(f"{size}\t{depth}\t{len(ok)} of {len(sel)}\t{stats(ok, 'wall_s')}\t"
                     f"{stats(ok, 'cpu_s')}\t{stats(ok, 'cores')}")
        cores[depth] = st.mean(float(r["cores"]) for r in ok) if ok else None
    if None not in cores.values():
        gaps.append(f"{size}\tgap in cores, depth 2 minus depth 1\t{cores[2] - cores[1]:.2f}")
failed = [f"{r['cell']} run {r['run']}: {r['error']}" for r in rows if not r["ok"]]
text = "\n".join(lines + [""] + gaps + ["", "failed runs: " + ("; ".join(failed) or "none")]) + "\n"
(HERE / "summary.txt").write_text(text)
print(text)
