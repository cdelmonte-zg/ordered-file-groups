"""Runner for the matrix of PLAN.md (round 5): cases, variants, pools.

Run from the repository root, with the two binaries in bin/ and the datasets of
scripts/generate_round5.sh in /tmp:

  python scripts/run_matrix.py --plan-check --out results/round-5   # plans only, once per case and variant
  python scripts/run_matrix.py --runs 10 --out results/round-5      # the measurement
  python scripts/run_matrix.py --only base A1-Q1 --runs 1 --out /tmp/dry   # a dry run

For every case, pool and variant: one unrecorded warm-up, then the recorded
runs with the variants in rotating order. Every run's SQL, output and stderr
are kept under <out>/<case>/. results.tsv has one row per run; summary.tsv one
row per case, pool and variant with medians and quartiles.
"""
import argparse
import csv
import os
import re
import signal
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BIN = ROOT / "bin"

QUERIES = {
    "Q1": "SELECT * FROM example ORDER BY col_1 ASC, col_2 ASC;",
    "Q2": """SELECT col_1, col_2, count(*) AS n, first_value(col_8) AS col_8
FROM example
GROUP BY col_1, col_2
ORDER BY col_1 ASC, col_2 ASC;""",
    "Q3": """SELECT
    col_1, col_2, col_3, col_4, col_5, col_6,
    first_value(col_7) AS col_7,
    first_value(col_8) AS col_8
FROM example
GROUP BY col_1, col_2, col_3, col_4, col_5, col_6
ORDER BY col_1 ASC, col_2 ASC;""",
    "Q4": """SELECT col_3, col_4, col_5, col_6, count(*) AS n
FROM example
GROUP BY col_3, col_4, col_5, col_6;""",
}

# variant -> (binary, split_file_groups_by_statistics, target: None = 2, "groups" = the groups needed)
VARIANTS = {
    "original": ("original", True, None),
    "accept-groups": ("accept-groups", True, None),
    "original-target": ("original", True, "groups"),
    "original-split-off": ("original", False, None),
}
DEFAULT_TARGET = 2

SQL = """SET datafusion.execution.target_partitions = {target};
SET datafusion.execution.split_file_groups_by_statistics = {split};

CREATE EXTERNAL TABLE example (
    col_1 VARCHAR NOT NULL,
    col_2 BIGINT NOT NULL,
    col_3 VARCHAR,
    col_4 VARCHAR,
    col_5 VARCHAR,
    col_6 VARCHAR NOT NULL,
    col_7 VARCHAR,
    col_8 DOUBLE
)
WITH ORDER (col_1 ASC, col_2 ASC)
STORED AS PARQUET
LOCATION '{location}';

{explain}
{query}
"""


@dataclass
class Case:
    name: str
    axis: str
    dataset: str          # directory under /tmp
    groups: int           # ordered groups the overlap needs (target of original-target)
    query: str = "Q3"
    pools: tuple = ("256m",)
    variants: tuple = ("original", "accept-groups", "original-target")
    note: str = ""
    produced: int = 0     # groups an ordered scan shows, when the bounds give another number

    @property
    def location(self):
        return f"/tmp/{self.dataset}/"


TWO = ("original", "accept-groups")
BASE = "df-16919-partial-12-depth-4"
ALL_POOLS = ("128m", "256m", "512m")

MATRIX = [
    Case("base", "base", BASE, 4, variants=("original", "accept-groups", "original-target",
                                             "original-split-off")),
    Case("A1-Q1", "A1", BASE, 4, query="Q1"),
    Case("A1-Q2", "A1", BASE, 4, query="Q2"),
    Case("A1-Q4", "A1", BASE, 4, query="Q4"),
    Case("A2-depth-1", "A2", "df-16919-partial-12-depth-1", 2, variants=TWO,
         note="two groups produced, same plan in both variants"),
    Case("A2-depth-2", "A2", "df-16919-partial-12-depth-2", 2, variants=TWO),
    Case("A2-depth-12", "A2", "df-16919-partial-12-depth-12", 12),
    Case("A3", "A3", BASE, 4, pools=("128m", "512m")),
    Case("A4-S0", "A4", "df-16919-partial-12-depth-4-S0", 4, pools=ALL_POOLS),
    Case("A4-S2", "A4", "df-16919-partial-12-depth-4-S2", 4, pools=ALL_POOLS),
    # A5: the 12-file points are the base case (depth 4) and A2-depth-12 (depth = files)
    Case("A5-120-depth-4", "A5", "df-16919-partial-120-depth-4-entity-rank", 4, variants=TWO),
    Case("A5-1200-depth-4", "A5", "df-16919-partial-1200-depth-4-entity-rank", 4, variants=TWO,
         produced=5, note="touching bounds force a fifth group"),
    Case("A5-120-depth-120", "A5", "df-16919-partial-120-depth-120-rank", 120, variants=TWO),
    Case("A5-1200-depth-1200", "A5", "df-16919-partial-1200-depth-1200-rank", 1200,
         variants=TWO, pools=ALL_POOLS, produced=1196),
    Case("A6-dup-0.5", "A6", "df-16919-partial-12-depth-4-dup0.5", 4),
]

UNITS = {"B": 1, "KB": 1 << 10, "MB": 1 << 20, "GB": 1 << 30}
OPERATORS = {  # column prefix -> operator name as displayed
    "scan": "DataSourceExec",
    "partial_agg": "AggregateExec.Partial",
    "repartition": "RepartitionExec",
    "final_agg": "AggregateExec.FinalPartitioned",
    "sort": "SortExec",
    "spm": "SortPreservingMergeExec",
}


def to_bytes(text):
    m = re.fullmatch(r"([\d.]+) ?([KMG]?B)", text.strip())
    return int(float(m.group(1)) * UNITS[m.group(2)]) if m else 0


def operator(line):
    m = re.search(r"\|\s+([A-Za-z]+Exec): ?(mode=\w+)?", line)
    if not m:
        return None
    name = m.group(1)
    if name == "AggregateExec":
        name += "." + m.group(2).split("=")[1]
    return name


def parse(out_text):
    """Plan features and per-operator metrics of an EXPLAIN ANALYZE output."""
    row = {"scan_groups": "", "sort_exec": 0, "preserve_order": 0,
           "partial_mode": "", "final_mode": ""}
    metrics = {}
    for line in out_text.splitlines():
        op = operator(line)
        if not op:
            continue
        if op == "SortExec":
            row["sort_exec"] += 1
        if op == "RepartitionExec" and "preserve_order=true" in line:
            row["preserve_order"] = 1
        if op == "DataSourceExec":
            m = re.search(r"file_groups=\{(\d+) groups?:", line)
            row["scan_groups"] = m.group(1) if m else ""
        for key, name in (("partial_mode", "AggregateExec.Partial"),
                          ("final_mode", "AggregateExec.FinalPartitioned")):
            if op == name:
                m = re.search(r"ordering_mode=(\w+(?:\(\[[\d, ]*\]\))?)", line)
                row[key] = m.group(1) if m else "Linear"
        count = re.search(r"\bspill_count=(\d+)", line)
        size = re.search(r"\bspilled_bytes=([\d.]+ ?[KMG]?B)", line)
        out = re.search(r"\boutput_bytes=([\d.]+ ?[KMG]?B)", line)
        c, b, o = metrics.get(op, (0, 0, 0))
        metrics[op] = (c + (int(count.group(1)) if count else 0),
                       b + (to_bytes(size.group(1)) if size else 0),
                       o + (to_bytes(out.group(1)) if out else 0))
    for key, name in OPERATORS.items():
        c, b, o = metrics.get(name, (0, 0, 0))
        if key not in ("scan", "spm"):
            row[f"{key}_spills"] = c
            row[f"{key}_spill_mb"] = round(b / (1 << 20), 1)
        row[f"{key}_out_mb"] = round(o / (1 << 20), 1)
    elapsed = re.findall(r"^Elapsed ([\d.]+) seconds\.$", out_text, re.M)
    # statements: SET ..., CREATE EXTERNAL TABLE, EXPLAIN ANALYZE. The CREATE is the
    # statement before the last one, however many SETs precede it.
    row["create_seconds"] = elapsed[-2] if len(elapsed) >= 4 else ""
    row["elapsed_seconds"] = elapsed[-1] if elapsed else ""
    return row


def sql_for(case, variant, explain="EXPLAIN ANALYZE"):
    _, split, target = VARIANTS[variant]
    return SQL.format(target=case.groups if target == "groups" else DEFAULT_TARGET,
                      split="true" if split else "false", location=case.location,
                      explain=explain, query=QUERIES[case.query])


def run(binary, sql_path, out_path, err_path, memory, timeout):
    cmd = ["/usr/bin/time", "-f", "BENCH_WALL_SECONDS=%e BENCH_MAX_RSS_KB=%M",
           str(binary), "--memory-limit", memory, "--mem-pool-type", "fair", "-f", str(sql_path)]
    # A session of its own, so that a timeout kills datafusion-cli and not only
    # /usr/bin/time, which is the process that subprocess would stop.
    with open(out_path, "w") as out, open(err_path, "w") as err:
        proc = subprocess.Popen(cmd, stdout=out, stderr=err, start_new_session=True)
        try:
            status = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait()
            status = 124
    out_text, err_text = Path(out_path).read_text(), Path(err_path).read_text()
    row = parse(out_text)
    wall = re.search(r"BENCH_WALL_SECONDS=([\d.]+)", err_text)
    rss = re.search(r"BENCH_MAX_RSS_KB=(\d+)", err_text)
    row["wall_seconds"] = wall.group(1) if wall else ""
    row["max_rss_mb"] = round(int(rss.group(1)) / 1024) if rss else ""
    failed = status != 0 or "Plan with Metrics" not in out_text \
        or re.search(r"Resources exhausted|\*\*Error\*\*|^Error:|^IO error", err_text, re.M)
    row["result"] = "failed" if failed else "ok"
    messages = [l for l in err_text.splitlines() if l.strip() and not l.startswith("BENCH_")]
    row["error"] = messages[0][:160] if failed and messages else ""
    if status == 124:
        row["error"] = f"timeout after {timeout} s, process group killed"
    if failed:
        row["elapsed_seconds"] = ""   # a failed statement prints no Elapsed of its own
    return row


def binary_of(variant):
    return BIN / f"datafusion-cli-{VARIANTS[variant][0]}-release"


def expected_plan(case, variant):
    """Every plan property the round relies on, as the plan must show it."""
    _, split, target = VARIANTS[variant]
    raised = case.groups if target == "groups" else DEFAULT_TARGET
    if case.query == "Q4":            # the ordering is projected away, no sort needed
        return {"sort_exec": 0, "output_ordering": 0, "scan_groups": str(raised),
                "preserve_order": 0, "partial_mode": "Linear", "final_mode": "Linear"}
    ordered = split and (target == "groups" or case.groups <= DEFAULT_TARGET
                         or variant == "accept-groups")
    if not ordered:
        mode = "" if case.query == "Q1" else "Linear"
        return {"sort_exec": 1, "output_ordering": 0, "scan_groups": str(DEFAULT_TARGET),
                "preserve_order": 0, "partial_mode": mode, "final_mode": mode}
    mode = {"Q1": "", "Q2": "Sorted", "Q3": "PartiallySorted([0, 1])"}[case.query]
    groups = case.produced or max(case.groups, DEFAULT_TARGET)
    return {"sort_exec": 0, "output_ordering": 1, "scan_groups": str(groups),
            "preserve_order": 0 if case.query == "Q1" else 1,
            "partial_mode": mode, "final_mode": mode}


def check_plan(case, variant, text):
    """(features, problems) of one EXPLAIN FORMAT INDENT output."""
    feats = parse(text)
    feats = {k: feats[k] for k in ("scan_groups", "sort_exec", "preserve_order",
                                   "partial_mode", "final_mode")}
    feats["output_ordering"] = int("output_ordering=" in text)
    problems = []
    if "physical_plan" not in text:
        problems.append("no physical plan")
    for key, want in expected_plan(case, variant).items():
        if feats[key] != want:
            problems.append(f"{key}: expected {want!r}, found {feats[key]!r}")
    return feats, problems


def write_plan_check(rows, out, name="plan-check.tsv"):
    with (out / name).open("w") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter="\t")
        w.writeheader()
        w.writerows(rows)
    bad = [r for r in rows if r["check"] != "ok"]
    if bad:
        print(f"{len(bad)} plan(s) not as expected", file=sys.stderr)
        sys.exit(1)


def plan_row(case, variant, feats, problems):
    return {"case": case.name, "variant": variant, "query": case.query,
            "scan_groups": feats["scan_groups"], "sort_exec": feats["sort_exec"],
            "preserve_order": feats["preserve_order"],
            "partial_mode": feats["partial_mode"], "final_mode": feats["final_mode"],
            "output_ordering": feats["output_ordering"],
            "check": "ok" if not problems else "; ".join(problems)}


def plan_check(cases, out):
    """EXPLAIN FORMAT INDENT once per case and variant, validated against expectations.

    Checks the sort, the advertised ordering, the scan groups, the aggregate modes
    and preserve_order. Writes plan-check.tsv with a `check` column ("ok" or what
    differs) and exits with status 1 when any plan is missing, failed or unexpected.
    """
    rows = []
    for case in cases:
        d = out / case.name / "plan-check"
        d.mkdir(parents=True, exist_ok=True)
        for variant in case.variants:
            sql_path = d / f"{variant}.sql"
            sql_path.write_text(sql_for(case, variant, "EXPLAIN FORMAT INDENT"))
            with open(d / f"{variant}.out", "w") as o, open(d / f"{variant}.err", "w") as e:
                status = subprocess.run([str(binary_of(variant)), "-f", str(sql_path)],
                                        stdout=o, stderr=e).returncode
            err = (d / f"{variant}.err").read_text()
            feats, problems = check_plan(case, variant, (d / f"{variant}.out").read_text())
            if status != 0:
                problems.insert(0, f"exit {status}")
            if re.search(r"Error|error:", err):
                problems.append("stderr: " + err.strip().splitlines()[0][:80])
            rows.append(plan_row(case, variant, feats, problems))
            print("\t".join(str(rows[-1][k]) for k in rows[-1]), flush=True)
    write_plan_check(rows, out)


def recheck_plans(cases, out):
    """Validate the plan outputs already recorded under `out`, without running anything.

    Prints one line per plan and exits with status 1 when any differs. Writes nothing.
    """
    bad = 0
    for case in cases:
        for variant in case.variants:
            path = out / case.name / "plan-check" / f"{variant}.out"
            if not path.is_file():
                feats, problems = {}, ["no recorded plan"]
            else:
                feats, problems = check_plan(case, variant, path.read_text())
            bad += bool(problems)
            print(case.name, variant, "ok" if not problems else "; ".join(problems), sep="\t")
    if bad:
        print(f"{bad} plan(s) not as expected", file=sys.stderr)
        sys.exit(1)


def plan_check_passed(cases, out):
    """True when plan-check.tsv covers every case and variant with check == ok."""
    path = out / "plan-check.tsv"
    if not path.exists():
        return False
    checked = {(r["case"], r["variant"]): r.get("check", "") for r in
               csv.DictReader(path.open(), delimiter="\t")}
    return all(checked.get((c.name, v)) == "ok" for c in cases for v in c.variants)


def record_machine(out):
    """What the measurements ran on, read later by make_report.py."""
    cpu = next((l.split(":", 1)[1].strip() for l in
                subprocess.run(["lscpu"], capture_output=True, text=True).stdout.splitlines()
                if l.startswith("Model name")), "unknown CPU")
    threads = subprocess.run(["nproc"], capture_output=True, text=True).stdout.strip()
    kernel = subprocess.run(["uname", "-r"], capture_output=True, text=True).stdout.strip()
    hashes = subprocess.run(["sha256sum"] + sorted(str(p) for p in BIN.glob("datafusion-cli-*")),
                            capture_output=True, text=True).stdout
    (out / "machine.txt").write_text(
        f"cpu\t{cpu}\nthreads\t{threads}\nkernel\tLinux {kernel}\n"
        f"date\t{time.strftime('%Y-%m-%d %H:%M %Z')}\nbinaries\n{hashes}")


COLUMNS = ["case", "axis", "query", "dataset", "pool", "variant", "run", "result", "scan_groups",
           "sort_exec", "preserve_order", "partial_mode", "final_mode", "create_seconds",
           "elapsed_seconds", "wall_seconds", "max_rss_mb",
           "sort_spills", "sort_spill_mb", "partial_agg_spills", "partial_agg_spill_mb",
           "final_agg_spills", "final_agg_spill_mb", "repartition_spills", "repartition_spill_mb",
           "scan_out_mb", "partial_agg_out_mb", "repartition_out_mb", "final_agg_out_mb",
           "sort_out_mb", "spm_out_mb", "error"]


def quartiles(values):
    values = sorted(float(v) for v in values if v != "")
    if not values:
        return ("", "", "")
    q = statistics.quantiles(values, n=4, method="inclusive") if len(values) > 1 else [values[0]] * 3
    return (f"{q[0]:.3f}", f"{statistics.median(values):.3f}", f"{q[2]:.3f}")


def summarize(rows, path):
    keys = []
    for r in rows:
        k = (r["case"], r["pool"], r["variant"])
        if k not in keys:
            keys.append(k)
    out = ["\t".join(["case", "axis", "query", "pool", "variant", "ok", "groups", "sort",
                      "preserve_order", "partial_mode", "final_mode",
                      "elapsed_q1", "elapsed_med", "elapsed_q3", "create_med", "rss_mb_med",
                      "sort_spills", "sort_spill_mb", "partial_agg_spills", "partial_agg_spill_mb",
                      "final_agg_spills", "final_agg_spill_mb", "repartition_spills",
                      "repartition_spill_mb", "scan_out_mb", "partial_agg_out_mb",
                      "repartition_out_mb", "final_agg_out_mb", "error"])]
    def med(sel, k, digits=1):
        values = [float(r[k]) for r in sel if r[k] != ""]
        return f"{statistics.median(values):.{digits}f}" if values else ""
    for case, pool, variant in keys:
        sel = [r for r in rows if (r["case"], r["pool"], r["variant"]) == (case, pool, variant)]
        ok = [r for r in sel if r["result"] == "ok"]
        ref = ok or sel
        q1, m, q3 = quartiles([r["elapsed_seconds"] for r in ok])
        out.append("\t".join(str(x) for x in [
            case, sel[0]["axis"], sel[0]["query"], pool, variant, f"{len(ok)}/{len(sel)}",
            ref[0]["scan_groups"], ref[0]["sort_exec"], ref[0]["preserve_order"],
            ref[0]["partial_mode"], ref[0]["final_mode"], q1, m, q3,
            med(ok, "create_seconds", 3), med(ref, "max_rss_mb"),
            med(ok, "sort_spills"), med(ok, "sort_spill_mb"),
            med(ok, "partial_agg_spills"), med(ok, "partial_agg_spill_mb"),
            med(ok, "final_agg_spills"), med(ok, "final_agg_spill_mb"),
            med(ok, "repartition_spills"), med(ok, "repartition_spill_mb"),
            med(ok, "scan_out_mb"), med(ok, "partial_agg_out_mb"),
            med(ok, "repartition_out_mb"), med(ok, "final_agg_out_mb"),
            next((r["error"] for r in sel if r["error"]), ""),
        ]))
    path.write_text("\n".join(out) + "\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--runs", type=int, default=10)
    p.add_argument("--timeout", type=float, default=300)
    p.add_argument("--only", nargs="*", help="case names")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--plan-check", action="store_true")
    p.add_argument("--recheck-plans", action="store_true",
                   help="validate the plans already recorded under --out; runs nothing")
    args = p.parse_args()

    cases = [c for c in MATRIX if not args.only or c.name in args.only]
    if args.recheck_plans:
        recheck_plans(cases, args.out)
        return
    for v in VARIANTS.values():
        if not (BIN / f"datafusion-cli-{v[0]}-release").is_file():
            raise SystemExit(f"Missing binary for {v[0]} in {BIN}")
    for c in cases:
        if not Path(c.location).is_dir():
            raise SystemExit(f"Missing dataset {c.location}; run scripts/generate_round5.sh")
    args.out.mkdir(parents=True, exist_ok=True)
    if args.plan_check:
        plan_check(cases, args.out)
        return

    results = args.out / "results.tsv"
    if results.exists():
        raise SystemExit(f"{results} exists: a run would overwrite the summary of the whole "
                         f"round; use a new --out directory")
    if not plan_check_passed(cases, args.out):
        raise SystemExit(f"run the plan check first and let it pass: "
                         f"python scripts/run_matrix.py --plan-check --out {args.out}")
    record_machine(args.out)
    rows = []
    for case in cases:
        for pool in case.pools:
            d = args.out / case.name / pool
            d.mkdir(parents=True, exist_ok=True)
            for variant in case.variants:
                sql_path = d / f"{variant}.sql"
                sql_path.write_text(sql_for(case, variant))
                run(binary_of(variant), sql_path, d / f"{variant}-warmup.out",
                    d / f"{variant}-warmup.err", pool, args.timeout)
            for i in range(1, args.runs + 1):
                order = list(case.variants)
                order = order[i % len(order):] + order[:i % len(order)]
                for variant in order:
                    row = run(binary_of(variant), d / f"{variant}.sql", d / f"{variant}-{i}.out",
                              d / f"{variant}-{i}.err", pool, args.timeout)
                    rows.append({"case": case.name, "axis": case.axis, "query": case.query,
                                 "dataset": case.dataset, "pool": pool, "variant": variant,
                                 "run": i, **row})
            # write after every case and pool, so that a partial run is usable
            with results.open("w") as f:
                w = csv.DictWriter(f, fieldnames=COLUMNS, delimiter="\t", extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
            summarize(rows, args.out / "summary.tsv")
            print(f"done: {case.name} {pool}", flush=True)


if __name__ == "__main__":
    main()
