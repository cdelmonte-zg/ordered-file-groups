"""Benchmark runner for apache/datafusion#16919 (original vs accept-groups).

Run from the repository root:
  python scripts/run_bench.py --bin-dir bin --out results/<round> --memory 128m --tag mem-128m [--runs 10]

One unrecorded warm-up per dataset and variant, then alternating order.
Failures are recorded, not fatal.
"""
import argparse
import csv
import re
import statistics
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "results" / "runs"
VARIANTS = ("original", "accept-groups")
BINARIES = {v: HERE.parent / "bin" / f"datafusion-cli-{v}-release" for v in VARIANTS}

# name, location, target_partitions
DATASETS = [
    ("partial-12-depth-1", "/tmp/df-16919-partial-12-depth-1/", 2),
    ("partial-12-depth-2", "/tmp/df-16919-partial-12-depth-2/", 2),
    ("partial-12-depth-3", "/tmp/df-16919-partial-12-depth-3/", 2),
    ("partial-12-depth-4", "/tmp/df-16919-partial-12-depth-4/", 2),
    ("partial-12-depth-6", "/tmp/df-16919-partial-12-depth-6/", 2),
    ("partial-12-depth-12", "/tmp/df-16919-partial-12-depth-12/", 2),
    ("sorted-medium-3", "/tmp/df-16919-sorted-medium/", 2),
    ("overlap-medium-12", "/tmp/df-16919-overlap-medium-12/", 2),
    ("sorted-15-target10", "/tmp/df-16919-sorted-15-target10/", 10),
    ("overlap-1000-x-600", "/tmp/df-16919-overlap-1000-x-600/", 2),
]

SQL = """SET datafusion.execution.target_partitions = {target};
SET datafusion.execution.split_file_groups_by_statistics = true;

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

EXPLAIN ANALYZE
SELECT
    col_1, col_2, col_3, col_4, col_5, col_6,
    first_value(col_7) AS col_7,
    first_value(col_8) AS col_8
FROM example
GROUP BY col_1, col_2, col_3, col_4, col_5, col_6
ORDER BY col_1 ASC, col_2 ASC;
"""

UNITS = {"B": 1, "KB": 1 << 10, "MB": 1 << 20, "GB": 1 << 30}


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
    row = {"scan_groups": "", "sort_exec": 0, "ordering_mode": ""}
    spills = {}
    for line in out_text.splitlines():
        op = operator(line)
        if not op:
            continue
        if op == "SortExec":
            row["sort_exec"] = 1
        if op == "DataSourceExec":
            m = re.search(r"file_groups=\{(\d+) groups?:", line)
            row["scan_groups"] = m.group(1) if m else ""
        if op == "AggregateExec.FinalPartitioned":
            m = re.search(r"ordering_mode=(\w+(?:\(\[[\d, ]*\]\))?)", line)
            row["ordering_mode"] = m.group(1) if m else "Linear"
        count = re.search(r"\bspill_count=(\d+)", line)
        size = re.search(r"\bspilled_bytes=([\d.]+ ?[KMG]?B)", line)
        if count:
            c, b = spills.get(op, (0, 0))
            spills[op] = (c + int(count.group(1)), b + (to_bytes(size.group(1)) if size else 0))
    for key, op in (("sort", "SortExec"), ("final_agg", "AggregateExec.FinalPartitioned"),
                    ("partial_agg", "AggregateExec.Partial"), ("repartition", "RepartitionExec")):
        c, b = spills.get(op, (0, 0))
        row[f"{key}_spills"] = c
        row[f"{key}_spill_mb"] = round(b / (1 << 20), 1)
    elapsed = re.findall(r"^Elapsed ([\d.]+) seconds\.$", out_text, re.M)
    row["elapsed_seconds"] = elapsed[-1] if elapsed else ""
    return row


def run(variant, sql_path, out_path, err_path, memory, timeout):
    cmd = ["/usr/bin/time", "-f", "BENCH_WALL_SECONDS=%e BENCH_MAX_RSS_KB=%M",
           str(BINARIES[variant]), "--memory-limit", memory, "--mem-pool-type", "fair",
           "-f", str(sql_path)]
    with open(out_path, "w") as out, open(err_path, "w") as err:
        try:
            status = subprocess.run(cmd, stdout=out, stderr=err, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            status = 124
    out_text, err_text = Path(out_path).read_text(), Path(err_path).read_text()
    row = parse(out_text)
    wall = re.search(r"BENCH_WALL_SECONDS=([\d.]+)", err_text)
    rss = re.search(r"BENCH_MAX_RSS_KB=(\d+)", err_text)
    row["wall_seconds"] = wall.group(1) if wall else ""
    row["max_rss_mb"] = round(int(rss.group(1)) / 1024) if rss else ""
    failed = status != 0 or "Plan with Metrics" not in out_text \
        or re.search(r"Resources exhausted|\*\*Error\*\*|^Error:", err_text, re.M)
    row["result"] = "failed" if failed else "ok"
    messages = [l for l in err_text.splitlines() if l.strip() and not l.startswith("BENCH_")]
    row["error"] = messages[0][:160] if failed and messages else ""
    return row


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--runs", type=int, default=10)
    p.add_argument("--memory", default="128m")
    p.add_argument("--timeout", type=int, default=180)
    p.add_argument("--only", nargs="*")
    p.add_argument("--tag", default="runs")
    p.add_argument("--bin-dir", type=Path, help="directory with the two release binaries")
    p.add_argument("--out", type=Path, help="directory for results (default: results/runs)")
    args = p.parse_args()

    global OUT
    if args.out:
        OUT = args.out
    if args.bin_dir:
        for v in VARIANTS:
            BINARIES[v] = args.bin_dir / f"datafusion-cli-{v}-release"
    for binary in BINARIES.values():
        if not binary.is_file():
            raise SystemExit(f"Missing binary: {binary}")

    raw = OUT / args.tag
    raw.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, location, target in DATASETS:
        if args.only and name not in args.only:
            continue
        sql_path = raw / f"{name}.sql"
        sql_path.write_text(SQL.format(target=target, location=location))
        for variant in BINARIES:
            run(variant, sql_path, raw / f"{name}-{variant}-warmup.out",
                raw / f"{name}-{variant}-warmup.err", args.memory, args.timeout)
        for i in range(1, args.runs + 1):
            order = list(BINARIES) if i % 2 else list(BINARIES)[::-1]
            for variant in order:
                row = run(variant, sql_path, raw / f"{name}-{variant}-{i}.out",
                          raw / f"{name}-{variant}-{i}.err", args.memory, args.timeout)
                rows.append({"dataset": name, "target": target, "variant": variant,
                             "run": i, "memory": args.memory, **row})
        print(f"done: {name}", flush=True)

    columns = ["dataset", "target", "variant", "run", "memory", "result", "scan_groups",
               "sort_exec", "ordering_mode", "elapsed_seconds", "wall_seconds", "max_rss_mb",
               "sort_spills", "sort_spill_mb", "final_agg_spills", "final_agg_spill_mb",
               "partial_agg_spills", "partial_agg_spill_mb", "repartition_spills",
               "repartition_spill_mb", "error"]
    with (OUT / f"results-{args.tag}.tsv").open("w") as f:
        w = csv.DictWriter(f, fieldnames=columns, delimiter="\t")
        w.writeheader()
        w.writerows(rows)

    def med(values):
        values = [float(v) for v in values if v != ""]
        return statistics.median(values) if values else float("nan")

    summary = ["dataset\tvariant\tok\tgroups\tsort\tmode\telapsed_med\telapsed_min\telapsed_max"
               "\trss_mb_med\tsort_spills\tfinal_agg_spills\tfinal_agg_spill_mb"
               "\trepartition_spills\trepartition_spill_mb\terror"]
    for name, _, _ in DATASETS:
        for variant in BINARIES:
            sel = [r for r in rows if r["dataset"] == name and r["variant"] == variant]
            if not sel:
                continue
            ok = [r for r in sel if r["result"] == "ok"]
            ref = ok or sel
            t = [float(r["elapsed_seconds"]) for r in ok if r["elapsed_seconds"]]
            summary.append("\t".join(str(x) for x in [
                name, variant, f"{len(ok)}/{len(sel)}", ref[0]["scan_groups"],
                ref[0]["sort_exec"], ref[0]["ordering_mode"],
                f"{med(t):.3f}" if t else "", f"{min(t):.3f}" if t else "",
                f"{max(t):.3f}" if t else "",
                f"{med([r['max_rss_mb'] for r in ref]):.0f}",
                f"{med([r['sort_spills'] for r in ok]):.0f}" if ok else "",
                f"{med([r['final_agg_spills'] for r in ok]):.0f}" if ok else "",
                f"{med([r['final_agg_spill_mb'] for r in ok]):.1f}" if ok else "",
                f"{med([r['repartition_spills'] for r in ok]):.0f}" if ok else "",
                f"{med([r['repartition_spill_mb'] for r in ok]):.1f}" if ok else "",
                next((r["error"] for r in sel if r["error"]), ""),
            ]))
    (OUT / f"summary-{args.tag}.tsv").write_text("\n".join(summary) + "\n")
    print("\n".join(summary))


if __name__ == "__main__":
    main()
