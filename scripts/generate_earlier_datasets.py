"""Generate the fully overlapping datasets: every file drawn from the same pools.

Run from the repository root:
  python scripts/generate_earlier_datasets.py

- sorted-medium: 3 files of 200,000 rows (seed 1)
- sorted-15-target10: 15 files of 40,000 rows (seed 2), the case of the issue
- overlap-1000-x-600: 1000 files of 600 rows (seed 3)
- overlap-medium-12: twelve copies of the first 50,000 rows of sorted-medium
  with col_6 prefixed per copy, so that the files overlap completely on the
  sort key and differ on a grouping column
"""
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# name, seed, rows per file, files
DATASETS = [
    ("sorted-medium", 1, 200_000, 3),
    ("sorted-15-target10", 2, 40_000, 15),
    ("overlap-1000-x-600", 3, 600, 1000),
]

for name, seed, rows, files in DATASETS:
    out = Path(f"/tmp/df-16919-{name}")
    shutil.rmtree(out, ignore_errors=True)
    subprocess.run([sys.executable, str(HERE / "generate_base.py"), "--rows", str(rows),
                    "--files", str(files), "--seed", str(seed), "--output-dir", str(out)],
                   check=True)

shutil.rmtree("/tmp/df-16919-overlap-medium-12", ignore_errors=True)
subprocess.run([sys.executable, str(HERE / "generate_overlap_12_target_2.py")], check=True)
