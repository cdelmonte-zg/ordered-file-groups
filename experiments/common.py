"""What the experiment scripts share: the output directory and one timed run."""
import argparse
import csv
import ctypes
import os
import re
import resource
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_matrix as rm  # noqa: E402

TIME = re.compile(r"BENCH wall=([\d.]+) user=([\d.]+) sys=([\d.]+) rss_kb=(\d+) minor=(\d+)")
PR_SET_THP_DISABLE = 41


def without_huge_pages():
    """Switch transparent huge pages off for this process and those it starts.

    For use as preexec_fn: the setting is kept across exec and inherited by
    children. Needs no privilege and changes nothing on the machine.
    """
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_THP_DISABLE, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_THP_DISABLE) failed")


def groups_by_bounds(dataset):
    """The ordered groups the bounds of a dataset give, from its manifest."""
    path = ROOT / "results" / "manifests" / f"{dataset}.tsv"
    for line in path.read_text().splitlines():
        if line.startswith("# groups_by_bounds\t"):
            return int(line.split("\t")[1])
    raise SystemExit(f"{path}: no groups_by_bounds line")


def output_dir(name):
    """The directory an experiment writes to: --out, or results/experiments/<name>.

    It must be new or empty: a run never overwrites or mixes with an earlier one.
    """
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=ROOT / "results" / "experiments" / name)
    out = p.parse_args().out
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty: remove it or choose another --out")
    out.mkdir(parents=True, exist_ok=True)
    rm.pin()                  # the experiments run on the CPUs of the matrix
    return out


def repartition_time(out_text, metric):
    """A time metric summed over the RepartitionExec operators of a plan, in seconds.

    Summed as run_matrix.parse sums their spills and output bytes. Read with the
    parser of run_matrix.py; '' when the plan has none.
    """
    times = [value for operator, name, _, value, kind in rm.metrics_of(out_text)
             if operator.split("#")[0] == "RepartitionExec" and name == metric and kind == "seconds"]
    return round(sum(times), 4) if times else ""


def timed_run(binary, sql_path, stem, pool="256m", env=None, nofile=None, timeout=300,
              huge_pages=True):
    """Run one SQL file with one of the binaries of bin/ under /usr/bin/time.

    Writes <stem>.out and <stem>.err.

    Returns ok, the wall and CPU time of the whole process, the cores used (CPU
    over wall), the peak RSS, the Elapsed of the last statement and the plan
    features and operator metrics that run_matrix.parse reads. `nofile` sets the
    open-file limit of the child process; the caller checks that the hard limit
    allows it. `huge_pages=False` runs the process with transparent huge pages
    switched off for it alone.
    """
    cmd = ["/usr/bin/time", "-f", "BENCH wall=%e user=%U sys=%S rss_kb=%M minor=%R",
           *rm.cli_command(rm.binary(binary), pool, sql_path)]
    def before_exec():
        if nofile:
            resource.setrlimit(resource.RLIMIT_NOFILE, (nofile, nofile))
        if not huge_pages:
            without_huge_pages()

    status, timed_out = rm.run_process(cmd, f"{stem}.out", f"{stem}.err", timeout,
                            env=dict(os.environ, **(env or {})),
                            preexec_fn=before_exec if nofile or not huge_pages else None)
    out, err = Path(f"{stem}.out").read_text(), Path(f"{stem}.err").read_text()
    tm = TIME.search(err)
    failed, error = rm.outcome(status, timed_out, out, err, timeout)
    # no BENCH line (a killed process): the times are unknown, not zero
    wall, user, system = (float(tm.group(i)) for i in (1, 2, 3)) if tm else ("", "", "")
    feats = rm.features(out, sql_path, failed)
    row = {"ok": int(not failed), "wall_s": wall,
           "cpu_s": round(user + system, 2) if tm else "",
           "user_s": user, "sys_s": system,
           "cores": round((user + system) / wall, 3) if tm and wall else "",
           "rss_mb": round(int(tm.group(4)) / 1024) if tm else "",
           "minor_faults": int(tm.group(5)) if tm else "",
           "elapsed_s": feats["elapsed_seconds"],
           "scan_groups": feats["scan_groups"],
           "error": error}
    for key in ("sort_spills", "final_agg_spills", "repartition_spills", "repartition_out_mb"):
        row[key] = feats.get(key, "")
    # how long the inputs of the plan's repartitions (all of them, summed) waited to
    # hand their batches to the outputs
    row["repartition_send_s"] = "" if failed else repartition_time(out, "send_time")
    return row


def interleaved(configs, runs, one, label=""):
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
            row = one(cfg, str(i))
            if row is not None:
                rows.append(row)
        print(f"{label}round {i} done", flush=True)
    return rows


def write_tsv(path, rows):
    if not rows:                        # nothing recorded: an empty table, not a crash
        Path(path).write_text("")
        return
    with open(path, "w") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter="\t")
        w.writeheader()
        w.writerows(rows)


def read_tsv(path):
    with open(path) as f:
        return list(csv.DictReader(f, delimiter="\t"))


def sql_for(dataset, query="Q3", target=2, split="true", settings=()):
    """The statements of one timed run: the round's template plus extra SET lines."""
    sql = rm.SQL.format(target=target, split=split, explain="EXPLAIN ANALYZE",
                        location=f"/tmp/{dataset}/", query=rm.QUERIES[query])
    marker = f"SET datafusion.execution.split_file_groups_by_statistics = {split};\n"
    assert marker in sql
    extra = "".join(f"SET {name} = {value};\n" for name, value in settings)
    return sql.replace(marker, marker + extra)


NOVIEW = (("datafusion.execution.parquet.schema_force_view_types", "false"),
          ("datafusion.sql_parser.map_string_types_to_utf8view", "false"))

