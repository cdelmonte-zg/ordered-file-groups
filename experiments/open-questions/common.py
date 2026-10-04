"""What the scripts of this directory share: one timed run of datafusion-cli."""
import csv
import os
import re
import resource
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import run_matrix as rm  # noqa: E402

TIME = re.compile(r"BENCH wall=([\d.]+) user=([\d.]+) sys=([\d.]+) rss_kb=(\d+)")


def fresh(path):
    """Refuse to overwrite the results of an earlier run; returns the path."""
    if Path(path).exists():
        raise SystemExit(f"{path} exists: move the outputs of the earlier run to a dated "
                         f"subdirectory first (experiments/open-questions/archive.py)")
    return Path(path)


def timed_run(variant, sql_path, stem, pool="256m", env=None, nofile=None, timeout=300):
    """Run one SQL file under /usr/bin/time; write <stem>.out and <stem>.err.

    Returns ok, the wall and CPU time of the whole process, the cores used (CPU
    over wall), the peak RSS, the Elapsed of the last statement and the plan
    features that run_matrix.parse reads. `nofile` sets the open-file limit of
    the child process; the caller checks that the hard limit allows it.
    """
    cmd = ["/usr/bin/time", "-f", "BENCH wall=%e user=%U sys=%S rss_kb=%M",
           str(rm.binary_of(variant)), "--memory-limit", pool, "--mem-pool-type", "fair",
           "-f", str(sql_path)]
    limit = (lambda: resource.setrlimit(resource.RLIMIT_NOFILE, (nofile, nofile))) if nofile else None
    status = rm.run_process(cmd, f"{stem}.out", f"{stem}.err", timeout,
                            env=dict(os.environ, **(env or {})), preexec_fn=limit)
    out, err = Path(f"{stem}.out").read_text(), Path(f"{stem}.err").read_text()
    tm = TIME.search(err)
    failed = rm.run_failed(status, out, err)
    messages = [l for l in err.splitlines() if l.strip() and not l.startswith("BENCH ")]
    if status == 124:
        messages = [f"timeout after {timeout} s, process group killed"]
    wall, user, system = (float(tm.group(i)) for i in (1, 2, 3)) if tm else (0.0, 0.0, 0.0)
    feats = rm.parse(out, statements=Path(sql_path).read_text().count(";"))
    return {"ok": int(not failed), "wall_s": wall, "cpu_s": round(user + system, 2),
            "user_s": user, "sys_s": system,
            "cores": round((user + system) / wall, 3) if wall else "",
            "rss_mb": round(int(tm.group(4)) / 1024) if tm else "",
            "elapsed_s": "" if failed else feats["elapsed_seconds"],
            "scan_groups": feats["scan_groups"],
            "error": messages[0][:160] if failed and messages else ""}


def mean_sd(rows, key):
    """'mean (sd x)' of a column over the given rows; the sd needs two runs."""
    values = [float(r[key]) for r in rows if r[key] != ""]
    if not values:
        return "no completed run"
    sd = f"{st.stdev(values):.3f}" if len(values) > 1 else "n/a"
    return f"{st.mean(values):.3f} (sd {sd})"


def interleaved(configs, runs, one):
    """One unrecorded warm-up per configuration, then `runs` rounds over all of them.

    The order rotates from one round to the next, as in run_matrix.py, so that no
    configuration always follows the same neighbour.
    """
    for cfg in configs:
        one(cfg, "warmup")
    rows = []
    for i in range(1, runs + 1):
        k = i % len(configs)
        for cfg in configs[k:] + configs[:k]:
            rows.append(one(cfg, str(i)))
        print(f"round {i} done", flush=True)
    return rows


def write_tsv(path, rows):
    with open(path, "w") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter="\t")
        w.writeheader()
        w.writerows(rows)
