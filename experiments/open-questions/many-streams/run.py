"""Experiments E1 to E3 of PLAN.md in this directory. Run from the repository root:
  python experiments/open-questions/many-streams/run.py
Writes results.tsv here, one row per recorded run.
"""
import csv
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import run_matrix as rm  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "runs-plan"
OUT.mkdir(exist_ok=True)


def dataset(files):
    return f"/tmp/df-16919-partial-{files}-depth-{files}-rank/"


def sql(query, files, target=2):
    return rm.SQL.format(target=target, split="true", location=dataset(files),
                         explain="EXPLAIN ANALYZE", query=rm.QUERIES[query])


# name, experiment, binary, files, query, target, purge, runs
configs = []
for files in (150, 300, 600, 1200):
    for q in ("Q1", "Q2", "Q3"):
        configs.append((f"E1-{files}-{q}-ordered", "E1", "accept-groups", files, q, 2, None, 5))
        configs.append((f"E1-{files}-{q}-original", "E1", "original", files, q, 2, None, 3))
for t in (4, 8):
    configs.append((f"E2-1200-Q3-t{t}", "E2", "accept-groups", 1200, "Q3", t, None, 5))
for q in ("Q1", "Q3"):
    configs.append((f"E3-1200-{q}-purge0", "E3", "accept-groups", 1200, q, 2, "0", 5))

MI = re.compile(r"peak rss: ([\d.]+) (\w+), peak commit: ([\d.]+) (\w+)")
TIME = re.compile(r"BENCH user=([\d.]+) sys=([\d.]+) rss_kb=(\d+)")
UNIT = {"B": 1 / 2**20, "KiB": 1 / 1024, "MiB": 1, "GiB": 1024}


def one(cfg, tag):
    name, exp, binary, files, q, target, purge, _ = cfg
    sql_path = OUT / f"{name}.sql"
    sql_path.write_text(sql(q, files, target))
    env = dict(os.environ, MIMALLOC_SHOW_STATS="1")
    if purge is not None:
        env["MIMALLOC_PURGE_DELAY"] = purge
    cmd = ["/usr/bin/time", "-f", "BENCH user=%U sys=%S rss_kb=%M",
           str(rm.BIN / f"datafusion-cli-{binary}-release"), "--memory-limit", "256m",
           "--mem-pool-type", "fair", "-f", str(sql_path)]
    res = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=300)
    (OUT / f"{name}-{tag}.out").write_text(res.stdout)
    (OUT / f"{name}-{tag}.err").write_text(res.stderr)
    feats = rm.parse(res.stdout)
    mi, tm = MI.search(res.stderr), TIME.search(res.stderr)
    return {
        "name": name, "experiment": exp, "binary": binary, "files": files, "query": q,
        "target": target, "purge_delay": purge if purge is not None else "default",
        "run": tag, "ok": int("Plan with Metrics" in res.stdout and res.returncode == 0),
        "groups": feats["scan_groups"], "elapsed_s": feats["elapsed_seconds"],
        "rss_mb": round(int(tm.group(3)) / 1024) if tm else "",
        "mi_peak_rss_mb": round(float(mi.group(1)) * UNIT[mi.group(2)]) if mi else "",
        "mi_peak_commit_mb": round(float(mi.group(3)) * UNIT[mi.group(4)]) if mi else "",
        "user_s": tm.group(1) if tm else "", "sys_s": tm.group(2) if tm else "",
    }


for cfg in configs:
    one(cfg, "warmup")
rows = []
for i in range(1, 6):
    for cfg in configs:
        if i <= cfg[-1]:
            rows.append(one(cfg, str(i)))
    print(f"round {i} done", flush=True)
with (HERE / "results.tsv").open("w") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter="\t")
    w.writeheader()
    w.writerows(rows)
print(len(rows), "runs")
