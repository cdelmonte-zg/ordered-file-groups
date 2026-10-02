"""One variable: the length of the string codes in col_1, col_3 and col_4.

Record of 2026-10-02. It ran against the generator of commit 80e8a52, which drew
the codes at random with (prefix, digits, letters); the current generator renders
ids with the shapes S0/S1/S2 of PLAN.md and no longer has ENTITY_CODE and the
like, so this script does not run as is. The runs and results.tsv in runs/ are
the record.

Everything else fixed: same seed, 2 files of 300,000 rows, target 2, fair pool,
one binary. Variants:

  baseline     ENT-1234567-ABC (15), REF-1234567-AB (14), INS-123456-AB (13)
  refs-short   col_3 and col_4 at most 12 characters: REF-1234567 (11), INS-123456 (10)
  refs-12      col_3 and col_4 at exactly 12 characters
  refs-13      col_3 and col_4 at exactly 13 characters
  all-short    also col_1 at most 12 characters: E-1234567-AB (12)
  all-long     col_1 25 characters like the reporter's data: ENTITY-1234567890123-ABCD

Run from the repository root:
  python experiments/string-width/run.py --bin bin/datafusion-cli-original-release
"""
import argparse
import csv
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import generate_base  # noqa: E402
import run_bench  # noqa: E402

HERE = Path(__file__).resolve().parent
VARIANTS = {
    "baseline": dict(ENTITY_CODE=("ENT", 7, 3), REFERENCE_CODE=("REF", 7, 2), INSTANCE_CODE=("INS", 6, 2)),
    "refs-short": dict(ENTITY_CODE=("ENT", 7, 3), REFERENCE_CODE=("REF", 7, 0), INSTANCE_CODE=("INS", 6, 0)),
    "all-short": dict(ENTITY_CODE=("E", 7, 2), REFERENCE_CODE=("REF", 7, 0), INSTANCE_CODE=("INS", 6, 0)),
    "all-long": dict(ENTITY_CODE=("ENTITY", 13, 4), REFERENCE_CODE=("REF", 7, 2), INSTANCE_CODE=("INS", 6, 2)),
    # the threshold: col_3 and col_4 at exactly 12 and at exactly 13 characters
    "refs-12": dict(ENTITY_CODE=("ENT", 7, 3), REFERENCE_CODE=("REF", 8, 0), INSTANCE_CODE=("INS", 8, 0)),
    "refs-13": dict(ENTITY_CODE=("ENT", 7, 3), REFERENCE_CODE=("REF", 9, 0), INSTANCE_CODE=("INS", 9, 0)),
}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bin", type=Path, required=True)
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--memory", default="128m")
    p.add_argument("--seed", type=int, default=9)
    p.add_argument("--only", nargs="*", help="variant names to run (default: all)")
    args = p.parse_args()

    out = HERE / "runs"
    out.mkdir(exist_ok=True)
    rows = []
    for name, codes in VARIANTS.items():
        if args.only and name not in args.only:
            continue
        for k, v in codes.items():
            setattr(generate_base, k, v)
        rng = np.random.default_rng(args.seed)
        pools = generate_base.make_pools(rng)
        data = Path(f"/tmp/df-16919-strings-{name}")
        data.mkdir(parents=True, exist_ok=True)
        for old in data.glob("*.parquet"):
            old.unlink()
        for i in range(2):
            generate_base.write_sorted(generate_base.generate_rows(300_000, rng, pools),
                                       data / f"reproducible_data_{i}.parquet")
        lengths = {c: len(pools[c][0]) for c in ("entities", "references", "instances")}
        sql = out / f"{name}.sql"
        sql.write_text(run_bench.SQL.format(target=2, location=f"{data}/"))
        run_bench.BINARIES["bin"] = args.bin
        for i in range(args.runs + 1):
            tag = "warmup" if i == 0 else str(i)
            row = run_bench.run("bin", sql, out / f"{name}-{tag}.out", out / f"{name}-{tag}.err",
                                args.memory, 180)
            if i:
                rows.append({"variant": name, "lengths": str(lengths), "run": i, **row})
    columns = ["variant", "lengths", "run", "result", "elapsed_seconds", "max_rss_mb",
               "final_agg_spills", "final_agg_spill_mb", "repartition_spills", "error"]
    tsv = out / ("results-" + "-".join(args.only) + ".tsv" if args.only else "results.tsv")
    with tsv.open("w") as f:
        w = csv.DictWriter(f, fieldnames=columns, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        print("\t".join(str(r[k]) for k in columns))


if __name__ == "__main__":
    main()
