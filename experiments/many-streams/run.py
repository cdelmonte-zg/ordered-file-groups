"""The memory of the many-stream plan: E1 to E3 of DESIGN.md, and the probes.

Run from the repository root:
  python experiments/many-streams/run.py [--out DIR]

Datasets of 150, 300, 600 and 1200 files with total overlap, the same 600,000
rows, 256 MB. The peak RSS of this case moves from run to run, so the whole
set of experiments is repeated in three independent series, and the report
gives every coefficient per series.

- E1: the ordered plan (five runs) and the original plan (three runs) at every
  file count, for the scan with nothing above it (Q0, where the engine drives
  all the streams at once), the ORDER BY query (Q1, where the merge pulls
  from them), the GROUP BY on the sort key (Q2) and the deduplication (Q3).
- E2: Q3 at every file count with 4 and 8 output partitions (2 is E1), so that
  the streams and the outputs are crossed.
- E3: Q1 and Q3 at 1200 files with MIMALLOC_PURGE_DELAY=0.
- P: one-variable probes at 1200 files, three runs each in every series: the
  deduplication with plain Utf8 strings and with batches of 1024 rows.

Writes every output and results.tsv, one row per recorded run.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import NOVIEW, interleaved, output_dir, sql_for, timed_run, write_tsv  # noqa: E402

OUT = output_dir("many-streams")
SERIES = 3
MI = re.compile(r"peak rss: ([\d.]+) (\w+), peak commit: ([\d.]+) (\w+)")
UNIT = {"B": 1 / 2**20, "KiB": 1 / 1024, "MiB": 1, "GiB": 1024}


def dataset(files):
    return f"df-16919-partial-{files}-depth-{files}-rank"


# name, experiment, variant, files, query, target, purge, settings, runs
configs = []
for files in (150, 300, 600, 1200):
    for q in ("Q0", "Q1", "Q2", "Q3"):
        configs.append((f"E1-{files}-{q}-ordered", "E1", "accept-groups", files, q, 2, None, (), 5))
        configs.append((f"E1-{files}-{q}-original", "E1", "original", files, q, 2, None, (), 3))
    for t in (4, 8):
        configs.append((f"E2-{files}-Q3-t{t}", "E2", "accept-groups", files, "Q3", t, None, (), 5))
for q in ("Q1", "Q3"):
    configs.append((f"E3-1200-{q}-purge0", "E3", "accept-groups", 1200, q, 2, "0", (), 5))
configs.append(("P-1200-Q3-utf8", "P-utf8", "accept-groups", 1200, "Q3", 2, None, NOVIEW, 3))
configs.append(("P-1200-Q3-batch1024", "P-batch1024", "accept-groups", 1200, "Q3", 2, None,
                (("datafusion.execution.batch_size", "1024"),), 3))
for name, _, _, files, q, target, _, settings, _ in configs:
    (OUT / f"{name}.sql").write_text(sql_for(dataset(files), q, target=target, settings=settings))

rows = []
for series in range(1, SERIES + 1):
    def one(cfg, tag, series=series):
        name, exp, variant, files, q, target, purge, _, runs = cfg
        if tag != "warmup" and int(tag) > runs:
            return None                  # the baselines and the probes have three runs
        env = {"MIMALLOC_SHOW_STATS": "1"}
        if purge is not None:
            env["MIMALLOC_PURGE_DELAY"] = purge
        stem = OUT / f"{name}-s{series}-{tag}"
        r = timed_run(variant, OUT / f"{name}.sql", stem, env=env)
        mi = MI.search(Path(f"{stem}.err").read_text())
        return {"series": series, "name": name, "experiment": exp, "variant": variant,
                "files": files, "query": q, "target": target,
                "purge_delay": purge if purge is not None else "default", "run": tag,
                "ok": r["ok"], "groups": r["scan_groups"], "elapsed_s": r["elapsed_s"],
                "rss_mb": r["rss_mb"],
                "mi_peak_commit_mb": round(float(mi.group(3)) * UNIT[mi.group(4)]) if mi else "",
                "user_s": r["user_s"], "sys_s": r["sys_s"], "error": r["error"]}

    rows += interleaved(configs, 5, one, label=f"series {series}, ")
    write_tsv(OUT / "results.tsv", rows)       # after every series, so a crash keeps what was done
