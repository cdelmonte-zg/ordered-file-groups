"""Synthetic rows for the deduplication query of apache/datafusion#16919.

Eight columns, the schema of the issue:

    col_1  VARCHAR NOT NULL   entity id, a few dozen distinct values
    col_2  BIGINT  NOT NULL   timestamp in milliseconds, clustered per entity
    col_3  VARCHAR            reference code
    col_4  VARCHAR            instance id
    col_5  VARCHAR            category
    col_6  VARCHAR NOT NULL   metric name, ten per category plus five shared
    col_7  VARCHAR            context, null in about 5 percent of the rows
    col_8  DOUBLE             value

Rows repeat on (col_1, col_2) because every entity draws its timestamps from a
few clusters of 500 consecutive milliseconds; a small share repeats on all six
grouping columns, which is what the deduplication query removes. Everything is
derived from a seed and a fixed time origin, so a dataset is byte-identical
when regenerated.

As a script, writes `files` Parquet files of `rows` rows each, every file sorted
by (col_1, col_2) and all files drawn from the same pools, so that every file
overlaps every other on the sort key:

    python scripts/generate_base.py --rows 40000 --files 15 --seed 2 \
        --output-dir /tmp/df-16919-sorted-15-target10
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

TIME_ORIGIN = datetime(2026, 9, 29, tzinfo=timezone.utc)
ORIGIN_MS = int(TIME_ORIGIN.timestamp() * 1000)
SPAN_MS = 7 * 24 * 60 * 60 * 1000
CLUSTER_MS = 500

N_ENTITIES = 30
N_CLUSTERS = 50
N_REFERENCES = 20
N_INSTANCES = 50
N_CATEGORIES = 6
METRICS_PER_CATEGORY = 10
N_SHARED_METRICS = 5
N_CONTEXTS = 8
# (prefix, digits, letters) of the generated codes: ENT-1234567-ABC and so on
ENTITY_CODE = ("ENT", 7, 3)
REFERENCE_CODE = ("REF", 7, 2)
INSTANCE_CODE = ("INS", 6, 2)
NULL_CONTEXT_SHARE = 0.05

SCHEMA = pa.schema([
    ("col_1", pa.string(), False),
    ("col_2", pa.int64(), False),
    ("col_3", pa.string(), True),
    ("col_4", pa.string(), True),
    ("col_5", pa.string(), True),
    ("col_6", pa.string(), False),
    ("col_7", pa.string(), True),
    ("col_8", pa.float64(), True),
])

SORT_KEY = [("col_1", "ascending"), ("col_2", "ascending")]

LETTERS = np.array(list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))


def _codes(rng, n, prefix, digits, letters):
    """n distinct strings like PREFIX-1234567-KQZ."""
    seen = set()
    while len(seen) < n:
        number = rng.integers(10 ** (digits - 1), 10 ** digits)
        tail = "".join(rng.choice(LETTERS, letters))
        seen.add(f"{prefix}-{number}-{tail}" if letters else f"{prefix}-{number}")
    return np.array(sorted(seen))


def make_pools(rng):
    """Value pools shared by every file of a dataset."""
    categories = np.array([f"CAT_{i + 1}" for i in range(N_CATEGORIES)])
    metrics = [f"M{c + 1}_{i + 1:02d}" for c in range(N_CATEGORIES)
               for i in range(METRICS_PER_CATEGORY)]
    shared = [f"METRIC_{i + 1:02d}" for i in range(N_SHARED_METRICS)]
    cluster_base = rng.integers(ORIGIN_MS - SPAN_MS, ORIGIN_MS - CLUSTER_MS, N_CLUSTERS)
    # Each entity samples its timestamps from 5 to 10 of the clusters.
    entity_clusters = [rng.choice(cluster_base, rng.integers(5, 11), replace=False)
                       for _ in range(N_ENTITIES)]
    return {
        "entities": _codes(rng, N_ENTITIES, *ENTITY_CODE),
        "references": _codes(rng, N_REFERENCES, *REFERENCE_CODE),
        "instances": _codes(rng, N_INSTANCES, *INSTANCE_CODE),
        "categories": categories,
        "metrics": np.array(metrics),
        "shared_metrics": np.array(shared),
        "contexts": np.array([f"CONTEXT_{letter}" for letter in LETTERS[:N_CONTEXTS]]),
        "entity_clusters": entity_clusters,
    }


def generate_rows(rows, rng, pools, cluster_ms=CLUSTER_MS):
    """`rows` rows as a pyarrow Table, not sorted.

    `cluster_ms` is the width of a timestamp cluster: with 500, an entity has up
    to 5,000 distinct timestamps; with 1, only its 5 to 10 cluster bases.
    """
    entity = rng.integers(N_ENTITIES, size=rows)
    col_2 = np.empty(rows, dtype=np.int64)
    for e in range(N_ENTITIES):
        mask = entity == e
        clusters = pools["entity_clusters"][e]
        base = clusters[rng.integers(len(clusters), size=mask.sum())]
        col_2[mask] = base + rng.integers(cluster_ms, size=mask.sum())

    category = rng.integers(N_CATEGORIES, size=rows)
    # Nine times out of ten a metric of the row's category, otherwise a shared one.
    own = rng.random(rows) < 0.9
    metric_index = category * METRICS_PER_CATEGORY + rng.integers(METRICS_PER_CATEGORY, size=rows)
    col_6 = np.where(own, pools["metrics"][metric_index],
                     pools["shared_metrics"][rng.integers(N_SHARED_METRICS, size=rows)])

    context = pools["contexts"][rng.integers(N_CONTEXTS, size=rows)].astype(object)
    context[rng.random(rows) < NULL_CONTEXT_SHARE] = None

    return pa.table({
        "col_1": pools["entities"][entity],
        "col_2": col_2,
        "col_3": pools["references"][rng.integers(N_REFERENCES, size=rows)],
        "col_4": pools["instances"][rng.integers(N_INSTANCES, size=rows)],
        "col_5": pools["categories"][category],
        "col_6": col_6,
        "col_7": pa.array(context, type=pa.string()),
        "col_8": rng.uniform(-1000.0, 1000.0, size=rows),
    }, schema=SCHEMA)


def generate_table(rows, seed, cluster_ms=CLUSTER_MS):
    """One table of `rows` rows from `seed`, not sorted."""
    rng = np.random.default_rng(seed)
    return generate_rows(rows, rng, make_pools(rng), cluster_ms)


def write_sorted(table, path):
    """Sort by (col_1, col_2) and write with statistics, one row group per MiB of rows."""
    table = table.sort_by(SORT_KEY)
    pq.write_table(
        table, path, compression="zstd", write_statistics=True,
        use_dictionary=True, row_group_size=1_048_576, data_page_size=1_048_576,
    )
    return table


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--rows", type=int, required=True, help="rows per file")
    p.add_argument("--files", type=int, default=1)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--cluster-ms", type=int, default=CLUSTER_MS,
                   help="width of a timestamp cluster in milliseconds (default 500)")
    args = p.parse_args()

    rng = np.random.default_rng(args.seed)
    pools = make_pools(rng)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for old in args.output_dir.glob("*.parquet"):
        old.unlink()
    width = len(str(args.files - 1))
    for i in range(args.files):
        table = generate_rows(args.rows, rng, pools, args.cluster_ms)
        write_sorted(table, args.output_dir / f"reproducible_data_{i:0{width}d}.parquet")
    print(f"{args.output_dir}: {args.files} files of {args.rows} rows, seed {args.seed}, "
          f"cluster width {args.cluster_ms} ms")


if __name__ == "__main__":
    main()
