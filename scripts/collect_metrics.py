"""Collect every metric the engine reports, for every recorded run.

Run from the repository root (scripts/run_lab.sh does it before the report):
  python scripts/collect_metrics.py [--results DIR]

EXPLAIN ANALYZE prints, for every operator of a plan, the metrics DataFusion
keeps: rows, bytes and batches emitted, spills, the time spent computing and
the times specific to the operator. This script reads every output under the
results directory that holds such a plan and writes metrics.tsv there, one row
per run, operator and metric. Nothing is selected by hand: a metric the engine
adds or renames appears in the table by itself.

Columns: the output file (relative to the results directory), the operator
(with the aggregate's mode, and a number when an operator occurs more than
once in a plan), the metric, its value as printed, the value as a number and
its kind: seconds, bytes, count or percent. A metric that is not one number,
such as the pruning metrics ("12 total -> 12 matched"), is kept as printed
with the kind "text".
"""
import argparse
import csv
import re
from pathlib import Path

import run_matrix as rm

METRICS = re.compile(r"metrics=\[(.*)\]")
VALUE = re.compile(r"^(-?[\d.]+)\s*([A-Za-zµ%]*)$")
SECONDS = {"ns": 1e-9, "µs": 1e-6, "us": 1e-6, "ms": 1e-3, "s": 1.0}
BYTES = {"B": 1, "KB": 2**10, "MB": 2**20, "GB": 2**30, "TB": 2**40}
COUNTS = {"": 1, "K": 1e3, "M": 1e6}


def number(metric, text):
    """(value, kind) of a printed metric; ('', 'text') when it is not a number."""
    m = VALUE.match(text.strip())
    if not m:
        return "", "text"
    value, unit = float(m.group(1)), m.group(2)
    if unit in SECONDS and (unit != "s" or "time" in metric or "elapsed" in metric):
        return value * SECONDS[unit], "seconds"
    # The engine prints sizes with a space and binary units ("8.3 MB", "0.0 B") and
    # counts with a space and K, M or B for billions ("24.5 M"). "B" alone is the
    # one ambiguous unit: it is taken as bytes for the size metrics, which the
    # engine names with "bytes" (output_bytes, bytes_scanned), and as billions otherwise.
    if unit in BYTES and unit != "B":
        return value * BYTES[unit], "bytes"
    if unit == "B":
        return (value, "bytes") if "bytes" in metric else (value * 1e9, "count")
    if unit in COUNTS:
        return value * COUNTS[unit], "count"
    if unit == "%":
        return value, "percent"
    return "", "text"


def metrics_of(out_text):
    """Rows (operator, metric, printed value, number, kind) of one EXPLAIN ANALYZE output."""
    rows, seen = [], {}
    for line in out_text.splitlines():
        name = rm.operator(line)
        found = METRICS.search(line)
        if not name or not found:
            continue
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:
            name = f"{name}#{seen[name]}"
        for item in found.group(1).split(", "):
            metric, _, text = item.partition("=")
            if not text:
                continue
            value, kind = number(metric, text)
            rows.append((name, metric.strip(), text.strip(), value, kind))
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--results", type=Path, default=rm.ROOT / "results")
    args = p.parse_args()
    files = runs = 0
    with (args.results / "metrics.tsv").open("w") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["file", "operator", "metric", "printed", "value", "kind"])
        for path in sorted(args.results.rglob("*.out")):
            files += 1
            text = path.read_text(errors="replace")
            if "Plan with Metrics" not in text:
                continue                # a plan check, or a run that failed
            runs += 1
            for row in metrics_of(text):
                w.writerow([path.relative_to(args.results), *row])
    print(f"{runs} runs with metrics out of {files} outputs -> {args.results / 'metrics.tsv'}")


if __name__ == "__main__":
    main()
