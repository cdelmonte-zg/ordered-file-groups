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
from run_matrix import metrics_of, number  # noqa: F401  (the parser lives with operator())

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
