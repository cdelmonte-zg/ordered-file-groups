"""Regenerate the datasets measured on 2026-09-28 with a fixed seed and time origin.

Run from the repository root:
  python scripts/generate_earlier_datasets.py
"""
import random
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

from generate_partial_overlap import load_generator

# name, seed, rows per file, files
DATASETS = [
    ("sorted-medium", 1, 200_000, 3),
    ("sorted-15-target10", 2, 40_000, 15),
    ("overlap-1000-x-600", 3, 600, 1000),
]

for name, seed, rows, files in DATASETS:
    out = Path(f"/tmp/df-16919-{name}")
    shutil.rmtree(out, ignore_errors=True)
    gen = load_generator()
    random.seed(seed)
    sys.argv = ["generate_base.py", "--rows", str(rows), "--files", str(files),
                "--output-dir", str(out)]
    gen.main()

# Twelve copies of the same 50,000 rows, col_6 prefixed: total overlap.
shutil.rmtree("/tmp/df-16919-overlap-medium-12", ignore_errors=True)
subprocess.run([sys.executable, str(HERE / "generate_overlap_12_target_2.py")], check=True)
