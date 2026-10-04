"""Compare the rows the variants return, for every case and pool of the matrix.

Run from the repository root, with the datasets in /tmp:
  python scripts/check_results.py --out results/result-check

The timing runs use EXPLAIN ANALYZE, which returns a plan and no rows. This
script runs each query plainly and checks, per case and pool:

- the deterministic columns hold the same multiset of rows in every variant
  as in `original` (a SHA-256 of the rows, sorted, is recorded);
- for the queries with ORDER BY, the rows come back sorted by (col_1, col_2);
- for the deduplication, the number of rows equals the distinct grouping keys
  of the dataset manifest.

`first_value` over a group with several rows may legitimately differ between
plans, so those columns are left out of the comparison: Q1 compares all eight
columns, Q2 the key and the count, Q3 the six grouping columns, Q4 the four
grouping columns and the count. Writes result-check.tsv and exits with status
1 when any check fails. The row files are not kept.
"""
import argparse
import csv
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

import run_matrix as rm

COMPARED = {
    "Q1": ["col_1", "col_2", "col_3", "col_4", "col_5", "col_6", "col_7", "col_8"],
    "Q2": ["col_1", "col_2", "n"],
    "Q3": ["col_1", "col_2", "col_3", "col_4", "col_5", "col_6"],
    "Q4": ["col_3", "col_4", "col_5", "col_6", "n"],
}
ORDERED = {"Q1", "Q2", "Q3"}


def rows_of(case, variant, pool, timeout, scratch):
    """(table, error) for one plain run of the query.

    The rows are written with COPY to one Parquet file, which keeps their order
    and streams them: printed to the terminal, datafusion-cli would hold the
    whole result in the same memory pool and fail under the limits of the round.
    """
    out = scratch / "rows.parquet"
    out.unlink(missing_ok=True)
    query = rm.QUERIES[case.query]
    copy = f"COPY ({query.rstrip().rstrip(';')}) TO '{out}' STORED AS PARQUET;"
    sql = rm.sql_for(case, variant, explain="").replace(query, copy)
    cmd = [str(rm.binary_of(variant)), "-q", "--memory-limit", pool, "--mem-pool-type", "fair",
           "-c", sql]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"timeout after {timeout} s"
    err = res.stderr.strip()
    if res.returncode != 0 or err or not out.is_file():
        return None, (err.splitlines()[0][:160] if err else f"exit {res.returncode}, no rows")
    table = pq.read_table(out)
    out.unlink()
    return table, ""


def is_sorted(table):
    """True when the rows are in non-decreasing order of (col_1, col_2)."""
    c1 = table["col_1"].to_numpy(zero_copy_only=False).astype(str)
    c2 = table["col_2"].to_numpy(zero_copy_only=False).astype(np.int64)
    return bool(np.all((c1[:-1] < c1[1:]) | ((c1[:-1] == c1[1:]) & (c2[:-1] <= c2[1:]))))


def digest(table, columns):
    """SHA-256 of the compared columns as a multiset of rows.

    Every row becomes one string (columns cast to text, nulls marked, joined by
    a separator that the data does not contain); the strings are sorted, so the
    order of the rows and the physical layout of the columns do not matter.
    """
    text = pa.large_string()
    parts = [pc.fill_null(pc.cast(table[c], text), pa.scalar("\x00", text)) for c in columns]
    lines = pc.binary_join_element_wise(*parts, pa.scalar("\x1f", text)).combine_chunks()
    lines = lines.take(pc.sort_indices(lines))
    h = hashlib.sha256()
    for buf in lines.buffers()[1:]:
        h.update(buf)
    return h.hexdigest()


def distinct_keys(dataset):
    for line in (rm.ROOT / "results" / "manifests" / f"{dataset}.tsv").read_text().splitlines():
        if line.startswith("# distinct_grouping_keys"):
            return int(line.split("\t")[1])
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--only", nargs="*", help="case names")
    p.add_argument("--timeout", type=float, default=300)
    args = p.parse_args()
    cases = [c for c in rm.MATRIX if not args.only or c.name in args.only]
    unknown = set(args.only or ()) - {c.name for c in rm.MATRIX}
    if unknown or not cases:
        raise SystemExit(f"no such case: {', '.join(sorted(unknown)) or '(empty selection)'}")
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    with tempfile.TemporaryDirectory(prefix="df-16919-result-check-") as scratch:
        scratch = Path(scratch)
        for case in cases:
            columns = COMPARED[case.query]
            for pool in case.pools:
                reference = None
                for variant in case.variants:      # `original` is first in every case
                    table, error = rows_of(case, variant, pool, args.timeout, scratch)
                    row = {"case": case.name, "query": case.query, "pool": pool, "variant": variant,
                           "rows": "", "sha256_compared_columns": "", "sorted": "", "check": ""}
                    problems = []
                    if table is None:
                        problems.append("failed: " + error)
                    else:
                        row["rows"] = table.num_rows
                        row["sha256_compared_columns"] = digest(table, columns)
                        if case.query in ORDERED:
                            row["sorted"] = int(is_sorted(table))
                            if not row["sorted"]:
                                problems.append("not sorted by (col_1, col_2)")
                        if case.query == "Q3" and table.num_rows != distinct_keys(case.dataset):
                            problems.append(f"expected {distinct_keys(case.dataset)} rows")
                        if variant == "original":
                            reference = row["sha256_compared_columns"]
                        elif reference is None:
                            problems.append("no result of `original` to compare with")
                        elif row["sha256_compared_columns"] != reference:
                            problems.append("rows differ from `original`")
                    row["check"] = "ok" if not problems else "; ".join(problems)
                    rows.append(row)
                    print("\t".join(str(row[k]) for k in ("case", "pool", "variant", "rows", "check")),
                          flush=True)
    with (args.out / "result-check.tsv").open("w") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter="\t")
        w.writeheader()
        w.writerows(rows)
    bad = [r for r in rows if r["check"] != "ok"]
    if bad:
        print(f"{len(bad)} result(s) not as expected", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
