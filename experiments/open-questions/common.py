"""What the scripts of this directory share: one timed run of datafusion-cli."""
import os
import re
import resource
import statistics as st
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import run_matrix as rm  # noqa: E402

TIME = re.compile(r"BENCH wall=([\d.]+) user=([\d.]+) sys=([\d.]+) rss_kb=(\d+)")
FAILURE = re.compile(r"Resources exhausted|\*\*Error\*\*|^Error:|^IO error", re.M)


def timed_run(variant, sql_path, stem, pool="256m", env=None, nofile=None, timeout=300):
    """Run one SQL file under /usr/bin/time; write <stem>.out and <stem>.err.

    Returns ok, the wall and CPU time of the whole process, the cores used (CPU
    over wall), the peak RSS, the Elapsed of the last statement and the plan
    features that run_matrix.parse reads. `nofile` sets the open-file limit of
    the child process.
    """
    cmd = ["/usr/bin/time", "-f", "BENCH wall=%e user=%U sys=%S rss_kb=%M",
           str(rm.binary_of(variant)), "--memory-limit", pool, "--mem-pool-type", "fair",
           "-f", str(sql_path)]
    limit = (lambda: resource.setrlimit(resource.RLIMIT_NOFILE, (nofile, nofile))) if nofile else None
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                             env=dict(os.environ, **(env or {})), preexec_fn=limit)
        out, err, status = res.stdout, res.stderr, res.returncode
    except subprocess.TimeoutExpired:
        out, err, status = "", f"timeout after {timeout} s", 124
    Path(f"{stem}.out").write_text(out)
    Path(f"{stem}.err").write_text(err)
    tm = TIME.search(err)
    failed = status != 0 or "Plan with Metrics" not in out or FAILURE.search(err)
    messages = [l for l in err.splitlines() if l.strip() and not l.startswith("BENCH ")]
    wall, user, system = (float(tm.group(i)) for i in (1, 2, 3)) if tm else (0.0, 0.0, 0.0)
    feats = rm.parse(out, statements=Path(sql_path).read_text().count(";"))
    return {"ok": int(not failed), "wall_s": wall, "cpu_s": round(user + system, 2),
            "cores": round((user + system) / wall, 3) if wall else "",
            "rss_mb": round(int(tm.group(4)) / 1024) if tm else "",
            "elapsed_s": "" if failed else feats["elapsed_seconds"],
            "scan_groups": feats["scan_groups"],
            "error": messages[0][:160] if failed and messages else ""}


def mean_sd(rows, key):
    """'mean (sd x)' of a column over the given rows, or a note when there are none."""
    values = [float(r[key]) for r in rows if r[key] != ""]
    if not values:
        return "no completed run"
    return f"{st.mean(values):.3f} (sd {st.stdev(values) if len(values) > 1 else 0:.3f})"


def interleaved(configs, runs, one):
    """One unrecorded warm-up per configuration, then `runs` rounds over all of them."""
    for cfg in configs:
        one(cfg, "warmup")
    rows = []
    for i in range(1, runs + 1):
        for cfg in configs:
            rows.append(one(cfg, str(i)))
        print(f"round {i} done", flush=True)
    return rows
