"""The many-stream case seen from outside the process: RSS and open files over time.

Run from the repository root:
  python experiments/process/run.py [--out DIR]

The deduplication and the ORDER BY query on 1200 files with total overlap,
256 MB, both plans, three runs each. While datafusion-cli runs, this script samples /proc/<pid>: the
resident memory every few milliseconds and, less often, the open file
descriptors by kind (Parquet data files, temporary files of the spills, other).
Nothing in the engine is changed or instrumented. The sampling takes CPU from
the machine, so the times of these runs are not comparable with the matrix.

Writes every output, samples.tsv (one row per sample) and results.tsv (one row
per run: the peak, when it falls, what is open then).
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import output_dir, rm, sql_for, write_tsv  # noqa: E402

OUT = output_dir("process")
RUNS = 3
PAGE = os.sysconf("SC_PAGE_SIZE")
EVERY = 0.002             # seconds between two samples of the resident memory
FD_EVERY = 0.010          # seconds between two listings of the descriptors
TMP = os.path.realpath(os.environ.get("TMPDIR", "/tmp"))
TIMEOUT = 300
DATASET = "df-16919-partial-1200-depth-1200-rank"

QUERIES = {"Q3": "deduplication", "Q1": "ORDER BY only"}
for query in QUERIES:
    (OUT / f"{query}.sql").write_text(sql_for(DATASET, query))


def descriptors(pid):
    """Open descriptors of a process by kind; None when it is gone."""
    counts = {"parquet": 0, "temp": 0, "other": 0}
    try:
        names = os.listdir(f"/proc/{pid}/fd")
    except OSError:
        return None
    for name in names:
        try:
            target = os.readlink(f"/proc/{pid}/fd/{name}")
        except OSError:
            continue
        target = target.split(" (deleted)")[0]
        if target.endswith(".parquet"):
            counts["parquet"] += 1
        elif os.path.realpath(target).startswith(TMP + "/") and Path(target).name.startswith(".tmp"):
            counts["temp"] += 1           # the spill files of the engine's disk manager
        else:
            counts["other"] += 1
    return counts


def rss_mb(pid):
    try:
        with open(f"/proc/{pid}/statm") as f:
            return int(f.read().split()[1]) * PAGE / 2**20
    except (OSError, IndexError, ValueError):
        return None


def one(query, variant, run):
    stem = OUT / f"{query}-{variant}-{run}"
    sql_path = OUT / f"{query}.sql"
    killed = False
    with open(f"{stem}.out", "w") as out, open(f"{stem}.err", "w") as err:
        start = time.monotonic()
        # not rm.run_process: this loop samples the process while it runs
        proc = subprocess.Popen(rm.cli_command(rm.binary(variant), "256m", sql_path),
                                stdout=out, stderr=err, start_new_session=True)
        samples, fds, next_fds = [], {"parquet": 0, "temp": 0, "other": 0}, 0.0
        while proc.poll() is None:
            now = time.monotonic() - start
            rss = rss_mb(proc.pid)
            if rss is None:
                break
            if now >= next_fds:
                fds = descriptors(proc.pid) or fds
                next_fds = now + FD_EVERY
            samples.append({"query": query, "variant": variant, "run": run, "t_s": round(now, 4),
                            "rss_mb": round(rss, 1), "fd_parquet": fds["parquet"],
                            "fd_temp": fds["temp"], "fd_other": fds["other"]})
            if now > TIMEOUT:                 # as the other runners: kill the whole group
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                killed = True
                break
            time.sleep(EVERY)
        status = proc.wait()
        timed_out = killed and status == -signal.SIGKILL    # not if it ended by itself first
        duration = time.monotonic() - start
    out_text, err_text = Path(f"{stem}.out").read_text(), Path(f"{stem}.err").read_text()
    failed, error = rm.outcome(status, timed_out, out_text, err_text, TIMEOUT)
    if not failed and not samples:
        failed, error = True, "the run left no sample"
    row = {"query": query, "variant": variant, "run": run, "ok": int(not failed),
           "duration_s": round(duration, 3), "samples": len(samples),
           "scan_groups": rm.features(out_text, sql_path, failed)["scan_groups"],
           "error": error}
    if samples:
        peak = max(samples, key=lambda s: s["rss_mb"])
        most_files = max(samples, key=lambda s: s["fd_parquet"])
        row.update({
            "peak_rss_mb": peak["rss_mb"], "peak_at_s": peak["t_s"],
            "peak_at_share_of_run": round(peak["t_s"] / duration, 2),
            "parquet_open_at_peak": peak["fd_parquet"], "temp_open_at_peak": peak["fd_temp"],
            "max_parquet_open": most_files["fd_parquet"],
            "rss_mb_when_most_parquet_open": most_files["rss_mb"],
            "max_temp_open": max(s["fd_temp"] for s in samples),
            "max_descriptors": max(s["fd_parquet"] + s["fd_temp"] + s["fd_other"] for s in samples),
        })
    return row, samples


rows, all_samples = [], []
cells = [(q, v) for q in QUERIES for v in ("original", "accept-groups")]
for query, variant in cells:
    one(query, variant, "warmup")
for run in range(1, RUNS + 1):
    for query, variant in cells:
        row, samples = one(query, variant, run)
        rows.append(row)
        all_samples += samples
        print(query, variant, run, "ok" if row["ok"] else "failed", row.get("peak_rss_mb", ""), flush=True)
first = ("query", "variant", "run", "ok")
summary = ("peak_rss_mb", "peak_at_s", "peak_at_share_of_run", "parquet_open_at_peak",
           "temp_open_at_peak", "max_parquet_open", "rss_mb_when_most_parquet_open",
           "max_temp_open", "max_descriptors")      # present even when no run left a sample
columns = sorted({k for r in rows for k in r} | set(summary), key=lambda k: (k not in first, k))
write_tsv(OUT / "results.tsv", [{k: r.get(k, "") for k in columns} for r in rows])
write_tsv(OUT / "samples.tsv", all_samples)
