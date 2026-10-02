"""Partially overlapping sorted files for apache/datafusion#16919.

Every dataset holds the SAME rows (one base table, generated once and cached);
only the assignment of rows to files changes. Each file is sorted by
(col_1, col_2). `--depth d` controls how many consecutive files share a key
range: file i and file i+d are disjoint, file i and file i+d-1 overlap.
So d is also the minimum number of file groups that keep the ordering.

Overlap is built on col_1, because DataFusion assembles the bounds of a file
from per-column min/max: (min col_1, min col_2) and (max col_1, max col_2).

Run from the repository root:
  python scripts/generate_partial_overlap.py --depth 4
"""
import argparse
import importlib.util
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
GENERATOR = HERE / "generate_base.py"
# The generator derives its timestamps from the current time; pin it.
TIME_ORIGIN = datetime(2026, 9, 29, tzinfo=timezone.utc)


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return TIME_ORIGIN


def load_generator():
    spec = importlib.util.spec_from_file_location("gen16919", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.datetime = FixedDateTime
    return module


def base_table(gen, rows, seed, cache):
    if cache.exists():
        return pd.read_parquet(cache)
    random.seed(seed)
    pools = gen.initialize_pools(rows)
    df = gen.generate_time_series_data(rows, pools)
    df = df.sort_values(by=["col_1", "col_2"], kind="stable").reset_index(drop=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    gen.save_with_schema(df, str(cache), "zstd")
    return pd.read_parquet(cache)


def expected_groups(bounds, target):
    """Same placement rule as split_groups_by_statistics_with_target_partitions."""
    order = sorted(range(len(bounds)), key=lambda i: bounds[i][0])
    groups = [[] for _ in range(target)]
    for i in order:
        eligible = [g for g in groups if not g or bounds[i][0] > bounds[g[-1]][1]]
        if eligible:
            min(eligible, key=len).append(i)
        else:
            groups.append([i])
    return [g for g in groups if g]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--files", type=int, default=12)
    p.add_argument("--depth", type=int, required=True)
    p.add_argument("--rows", type=int, default=600_000, help="total rows")
    p.add_argument("--seed", type=int, default=16919)
    p.add_argument("--target", type=int, default=2)
    p.add_argument("--output-dir", type=Path)
    p.add_argument("--cache", type=Path)
    args = p.parse_args()
    if not 1 <= args.depth <= args.files:
        sys.exit("depth must be between 1 and the number of files")

    out = args.output_dir or Path(f"/tmp/df-16919-partial-{args.files}-depth-{args.depth}")
    cache = args.cache or Path(f"/tmp/df-16919-base/base-{args.rows}-seed-{args.seed}-origin-2026-09-29.parquet")
    gen = load_generator()
    df = base_table(gen, args.rows, args.seed, cache)

    entities = sorted(df["col_1"].unique())
    slots = args.files - 1 + args.depth
    if slots > len(entities):
        sys.exit(f"{slots} slots need at least {slots} distinct col_1 values")
    slot_of_entity = {e: (i * slots) // len(entities) for i, e in enumerate(entities)}
    slot = df["col_1"].map(slot_of_entity).to_numpy()

    # A row in slot k may go to any file i with i <= k < i + depth.
    low = np.maximum(0, slot - args.depth + 1)
    high = np.minimum(args.files - 1, slot)
    rng = np.random.default_rng(args.seed)
    file_of_row = low + (rng.random(len(df)) * (high - low + 1)).astype(np.int64)

    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.parquet"):
        old.unlink()

    bounds = []
    manifest = ["file\trows\tmin_col_1\tmin_col_2\tmax_col_1\tmax_col_2"]
    for i in range(args.files):
        part = df[file_of_row == i].reset_index(drop=True)
        if part.empty:
            sys.exit(f"file {i} would be empty")
        assert pd.MultiIndex.from_frame(part[["col_1", "col_2"]]).is_monotonic_increasing
        gen.save_with_schema(part, str(out / f"reproducible_data_{i:02d}.parquet"), "zstd")
        lo = (part["col_1"].min(), int(part["col_2"].min()))
        hi = (part["col_1"].max(), int(part["col_2"].max()))
        bounds.append((lo, hi))
        manifest.append(f"{i}\t{len(part)}\t{lo[0]}\t{lo[1]}\t{hi[0]}\t{hi[1]}")

    groups = expected_groups(bounds, args.target)
    # Not inside the data directory: the listing table would read it as Parquet.
    manifests = HERE.parent / "results" / "manifests"
    manifests.mkdir(exist_ok=True)
    (manifests / f"{out.name}.tsv").write_text("\n".join(manifest) + "\n")
    sizes = [int((file_of_row == i).sum()) for i in range(args.files)]
    print(f"{out}: {args.files} files, depth {args.depth}, {len(df)} rows, "
          f"rows per file {min(sizes)}..{max(sizes)}, "
          f"expected groups {len(groups)} (target {args.target}): {groups}")


if __name__ == "__main__":
    main()
