"""Sorted files with a controlled overlap of their (col_1, col_2) ranges.

Every dataset of one base table holds the SAME rows (generated once and
cached); only the assignment of rows to files changes. Each file is sorted by
(col_1, col_2). `--depth d` controls how many consecutive files share a key
range: file i and file i+d are disjoint, file i and file i+d-1 overlap, so d
is the minimum number of ordered file groups.

Three assignments (DESIGN.md, axis A5):

- `entity` (default): the overlap is built on col_1, whose distinct values are
  spread over the slots; a file's bounds are exact, because DataFusion assembles
  them from per-column min/max and every file begins and ends on an entity
  boundary. Needs at least files - 1 + depth distinct entities.
- `entity-rank`: for more files than entities. Every entity gets a run of
  consecutive files in proportion to its rows (at least `depth` files), and the
  overlap of depth d is built inside the entity on the rank of the row by
  col_2. Files hold one entity each, so the bounds are exact and the groups
  equal the depth.
- `rank`: the overlap is built on the rank of the row in the whole sorted
  order, for any number of files, used for depth = files (total overlap); a
  file that spans two entities carries a looser bound than its rows, so the
  groups the statistics produce can differ from the depth. The manifest
  reports both.

The manifest (results/manifests/<dataset>.tsv) records, per file, rows, bytes,
row groups and the min/max of col_1 and col_2; its header lines record the
distinct grouping keys, the duplicate share and, with copies, the share of
copies that fell into the same file as their original.

Run from the repository root:
  python scripts/generate_partial_overlap.py --depth 4
  python scripts/generate_partial_overlap.py --depth 4 --shape S2
  python scripts/generate_partial_overlap.py --depth 4 --duplicate-share 0.5
  python scripts/generate_partial_overlap.py --files 1200 --depth 4 --assign entity-rank
  python scripts/generate_partial_overlap.py --files 1200 --depth 1200 --assign rank
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from generate_base import (CLUSTER_MS, DEFAULT_SHAPE, ID_SORT_KEY, SHAPES, distinct_keys,
                           generate_table, render, write_sorted)

HERE = Path(__file__).resolve().parent
MANIFESTS = HERE.parent / "results" / "manifests"


def cache_record(rows, seed, duplicate_share, cluster_ms):
    """What a cached base table depends on: its parameters and the generator source."""
    source = (Path(__file__).resolve().parent / "generate_base.py").read_bytes()
    return {"rows": rows, "seed": seed, "duplicate_share": float(duplicate_share),
            "cluster_ms": cluster_ms,
            "generator_sha256": hashlib.sha256(source).hexdigest()}


def base_table(rows, seed, duplicate_share, cache, cluster_ms=CLUSTER_MS):
    """The one table every variant redistributes: integer ids, sorted, cached.

    What the cache depends on is recorded beside it, in `<cache>.params.json`, so
    that the Parquet file itself stays byte for byte what it was. A cache whose
    record is missing, unreadable or different is regenerated. The record is
    removed before the table is rewritten and written last, and both files are
    renamed into place, so an interrupted run leaves no cache that looks valid.
    """
    params = cache_record(rows, seed, duplicate_share, cluster_ms)
    record = cache.with_name(cache.name + ".params.json")
    if cache.exists():
        try:
            recorded = json.loads(record.read_text())
        except (OSError, ValueError):
            recorded = None
        if recorded == params:
            table = pq.read_table(cache)
            if table.num_rows == rows:        # a record beside another table
                return table
        print(f"cache {cache} is not recorded as generated with these parameters: "
              f"regenerating", file=sys.stderr)
    record.unlink(missing_ok=True)
    cache.unlink(missing_ok=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    table = generate_table(rows, seed, cluster_ms=cluster_ms, duplicate_share=duplicate_share)
    partial = cache.with_name(cache.name + ".partial")
    pq.write_table(table.sort_by(ID_SORT_KEY), partial, compression="zstd")
    partial.replace(cache)
    partial = record.with_name(record.name + ".partial")
    partial.write_text(json.dumps(params, sort_keys=True) + "\n")
    partial.replace(record)
    return pq.read_table(cache)


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


def assign(table, files, depth, how, rng):
    """File index of every row of the (sorted) base table."""
    n = table.num_rows
    slots = files - 1 + depth
    if how == "entity":
        entity = table["entity"].to_numpy()
        entities = np.unique(entity)
        if slots > len(entities):
            sys.exit(f"{slots} slots need at least {slots} distinct entities, "
                     f"the table has {len(entities)}; use --assign rank")
        slot_of_entity = (np.arange(len(entities)) * slots) // len(entities)
        slot = slot_of_entity[np.searchsorted(entities, entity)]
    elif how == "entity-rank":
        entity = table["entity"].to_numpy()
        entities, counts = np.unique(entity, return_counts=True)
        if files < depth * len(entities):
            sys.exit(f"{files} files are fewer than {depth} per entity for "
                     f"{len(entities)} entities; use --assign entity")
        # files per entity: at least `depth`, the rest in proportion to the rows
        per = np.full(len(entities), depth)
        spare = files - per.sum()
        share = np.floor(spare * counts / counts.sum()).astype(int)
        per += share
        for i in np.argsort(-(spare * counts / counts.sum() - share))[:files - per.sum()]:
            per[i] += 1
        first = np.concatenate([[0], np.cumsum(per)[:-1]])
        file_of_row = np.empty(n, dtype=np.int64)
        for e, f_e, start, r_e in zip(entities, per, first, counts):
            rows = np.flatnonzero(entity == e)  # already sorted by col_2 within the entity
            slot = (np.arange(r_e) * (f_e - 1 + depth)) // r_e
            low = np.maximum(0, slot - depth + 1)
            high = np.minimum(f_e - 1, slot)
            file_of_row[rows] = start + low + (rng.random(r_e) * (high - low + 1)).astype(np.int64)
        return file_of_row
    else:
        slot = (np.arange(n) * slots) // n
    # A row in slot k may go to any file i with i <= k < i + depth.
    low = np.maximum(0, slot - depth + 1)
    high = np.minimum(files - 1, slot)
    return low + (rng.random(n) * (high - low + 1)).astype(np.int64)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--files", type=int, default=12)
    p.add_argument("--depth", type=int, required=True)
    p.add_argument("--rows", type=int, default=600_000, help="total rows")
    p.add_argument("--seed", type=int, default=16919)
    p.add_argument("--target", type=int, default=2)
    p.add_argument("--shape", choices=sorted(SHAPES), default=DEFAULT_SHAPE)
    p.add_argument("--duplicate-share", type=float, default=0.0)
    p.add_argument("--cluster-ms", type=int, default=CLUSTER_MS,
                   help="width of a timestamp cluster: the distinct (col_1, col_2) prefixes "
                        "grow with it, the rows and the files do not")
    p.add_argument("--assign", choices=("entity", "entity-rank", "rank"), default="entity")
    p.add_argument("--name", help="dataset name (default: derived from the options)")
    p.add_argument("--output-dir", type=Path)
    p.add_argument("--cache", type=Path)
    args = p.parse_args()
    if not 1 <= args.depth <= args.files:
        sys.exit("depth must be between 1 and the number of files")

    name = args.name or "df-16919-partial-{}-depth-{}{}{}{}".format(
        args.files, args.depth,
        "" if args.shape == DEFAULT_SHAPE else f"-{args.shape}",
        "" if args.duplicate_share == 0 else f"-dup{args.duplicate_share:g}",
        "" if args.assign == "entity" else f"-{args.assign}") + (
        # a dataset of another size or cluster width never takes the name of the default one
        ("" if args.rows == 600_000 else f"-rows{args.rows}")
        + ("" if args.cluster_ms == CLUSTER_MS else f"-cluster{args.cluster_ms}"))
    out = args.output_dir or Path(f"/tmp/{name}")
    dup = f"-dup{args.duplicate_share:g}" if args.duplicate_share else ""
    cms = "" if args.cluster_ms == CLUSTER_MS else f"-cluster{args.cluster_ms}"
    cache = args.cache or Path(
        f"/tmp/df-16919-base/base-{args.rows}-seed-{args.seed}{dup}{cms}-origin-2026-09-29.parquet")
    table = base_table(args.rows, args.seed, args.duplicate_share, cache, args.cluster_ms)

    rng = np.random.default_rng(args.seed)
    file_of_row = assign(table, args.files, args.depth, args.assign, rng)

    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.parquet"):
        old.unlink()

    width = len(str(args.files - 1))
    bounds, lines, sizes = [], [], []
    for i in range(args.files):
        rows = np.flatnonzero(file_of_row == i)
        if len(rows) == 0:
            sys.exit(f"file {i} would be empty")
        part = render(table.take(pa.array(rows)), args.shape)
        path = out / f"reproducible_data_{i:0{width}d}.parquet"
        part = write_sorted(part, path)
        lo = (part["col_1"][0].as_py(), pc.min(part["col_2"]).as_py())
        hi = (part["col_1"][-1].as_py(), pc.max(part["col_2"]).as_py())
        bounds.append((lo, hi))
        meta = pq.ParquetFile(path).metadata
        sizes.append(len(rows))
        lines.append(f"{i}\t{len(rows)}\t{path.stat().st_size}\t{meta.num_row_groups}"
                     f"\t{lo[0]}\t{lo[1]}\t{hi[0]}\t{hi[1]}")

    groups = expected_groups(bounds, args.target)
    keys = distinct_keys(table)
    share = 1 - keys / table.num_rows
    header = [f"# dataset\t{name}", f"# files\t{args.files}", f"# depth\t{args.depth}",
              f"# assign\t{args.assign}", f"# shape\t{args.shape}", f"# rows\t{table.num_rows}",
              f"# distinct_grouping_keys\t{keys}", f"# duplicate_rows\t{table.num_rows - keys}",
              f"# duplicate_share\t{share:.6f}",
              f"# groups_by_bounds\t{len(groups)}\t{groups}"]
    if "origin" in table.column_names:
        # rid and origin are stable ids assigned before the sort; map rid -> file
        rid = table["rid"].to_numpy()
        origin = table["origin"].to_numpy()
        file_of_rid = np.empty(rid.max() + 1, dtype=np.int64)
        file_of_rid[rid] = file_of_row
        copies = origin >= 0
        same = (file_of_row[copies] == file_of_rid[origin[copies]]).mean()
        header.append(f"# copies\t{int(copies.sum())}\t# same_file_share\t{same:.4f}")
    # the sort prefix of the lab is (col_1, col_2): how many distinct ones, and so how
    # many distinct grouping keys an ordered aggregation holds open under one of them
    # (counted on the source table, before any distribution among partitions)
    grouping = [c for c in ("entity", "col_2", "reference", "instance", "col_5", "col_6")
                if c in table.column_names]
    distinct = table.select(grouping).group_by(grouping).aggregate([])
    per_prefix = distinct.group_by(["entity", "col_2"]).aggregate([([], "count_all")])
    per_prefix = per_prefix["count_all"].to_numpy()
    header += [f"# cluster_ms\t{args.cluster_ms}", f"# distinct_prefixes\t{len(per_prefix)}",
               f"# grouping_keys_per_prefix\t{per_prefix.mean():.2f}",
               f"# grouping_keys_per_prefix_median\t{np.median(per_prefix):.0f}",
               f"# grouping_keys_per_prefix_max\t{per_prefix.max()}"]
    MANIFESTS.mkdir(parents=True, exist_ok=True)
    (MANIFESTS / f"{name}.tsv").write_text(
        "\n".join(header) + "\nfile\trows\tbytes\trow_groups\tmin_col_1\tmin_col_2\tmax_col_1\tmax_col_2\n"
        + "\n".join(lines) + "\n")
    print(f"{out}: {args.files} files, depth {args.depth}, {args.assign}, shape {args.shape}, "
          f"{table.num_rows} rows, {keys} distinct keys ({table.num_rows - keys} duplicate rows), "
          f"rows per file {min(sizes)}..{max(sizes)}, groups by bounds {len(groups)} "
          f"(target {args.target})")


if __name__ == "__main__":
    main()
