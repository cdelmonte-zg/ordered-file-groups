"""Experiments E1 to E3 of PLAN.md in this directory. Run from the repository root:
  python experiments/open-questions/many-streams/run.py
Writes results.tsv here, one row per recorded run.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import fresh, interleaved, rm, timed_run, write_tsv  # noqa: E402

OUT = HERE / "runs-plan"
OUT.mkdir(exist_ok=True)
RESULTS = fresh(HERE / "results.tsv", outputs=OUT)


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
UNIT = {"B": 1 / 2**20, "KiB": 1 / 1024, "MiB": 1, "GiB": 1024}


def one(cfg, tag):
    name, exp, binary, files, q, target, purge, runs = cfg
    if tag != "warmup" and int(tag) > runs:
        return None                      # the baselines have three runs, not five
    sql_path = OUT / f"{name}.sql"
    sql_path.write_text(sql(q, files, target))
    env = {"MIMALLOC_SHOW_STATS": "1"}
    if purge is not None:
        env["MIMALLOC_PURGE_DELAY"] = purge
    r = timed_run(binary, sql_path, OUT / f"{name}-{tag}", env=env)
    mi = MI.search((OUT / f"{name}-{tag}.err").read_text())
    return {
        "name": name, "experiment": exp, "binary": binary, "files": files, "query": q,
        "target": target, "purge_delay": purge if purge is not None else "default",
        "run": tag, "ok": r["ok"], "groups": r["scan_groups"], "elapsed_s": r["elapsed_s"],
        "rss_mb": r["rss_mb"],
        "mi_peak_rss_mb": round(float(mi.group(1)) * UNIT[mi.group(2)]) if mi else "",
        "mi_peak_commit_mb": round(float(mi.group(3)) * UNIT[mi.group(4)]) if mi else "",
        "user_s": r["user_s"], "sys_s": r["sys_s"], "error": r["error"],
    }


rows = [r for r in interleaved(configs, 5, one) if r is not None]
write_tsv(RESULTS, rows)
print(len(rows), "runs")
