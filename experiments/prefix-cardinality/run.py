"""One variable: the width of the timestamp clusters, hence the number of distinct
(col_1, col_2) prefixes. Everything else fixed: same seed, 2 files of 300,000
rows drawn from the same pools, target 2, fair pool, one binary.

Run from the repository root:
  python experiments/prefix-cardinality/run.py --bin bin/datafusion-cli-original-release
"""
import argparse
import csv
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import run_bench  # noqa: E402

HERE = Path(__file__).resolve().parent
WIDTHS = (1, 500)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bin", type=Path, required=True)
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--memory", default="128m")
    p.add_argument("--seed", type=int, default=9)
    args = p.parse_args()

    out = HERE / "runs"
    out.mkdir(exist_ok=True)
    rows = []
    for width in WIDTHS:
        data = Path(f"/tmp/df-16919-prefix-{width}")
        subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_base.py"), "--rows", "300000",
                        "--files", "2", "--seed", str(args.seed), "--cluster-ms", str(width),
                        "--output-dir", str(data)], check=True)
        sql = out / f"width-{width}.sql"
        sql.write_text(run_bench.SQL.format(target=2, location=f"{data}/"))
        run_bench.BINARIES["bin"] = args.bin
        for i in range(args.runs + 1):
            tag = "warmup" if i == 0 else str(i)
            row = run_bench.run("bin", sql, out / f"width-{width}-{tag}.out",
                                out / f"width-{width}-{tag}.err", args.memory, 180)
            if i:
                rows.append({"cluster_ms": width, "run": i, **row})
    columns = ["cluster_ms", "run", "result", "scan_groups", "ordering_mode", "elapsed_seconds",
               "max_rss_mb", "final_agg_spills", "final_agg_spill_mb", "repartition_spills",
               "repartition_spill_mb", "error"]
    with (out / "results.tsv").open("w") as f:
        w = csv.DictWriter(f, fieldnames=columns, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        print({k: r[k] for k in columns})


if __name__ == "__main__":
    main()
