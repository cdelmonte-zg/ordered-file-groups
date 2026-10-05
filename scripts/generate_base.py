"""Synthetic rows for the queries of DESIGN.md, with the schema of apache/datafusion#16919.

    col_1  VARCHAR NOT NULL   entity id, a few dozen distinct values
    col_2  BIGINT  NOT NULL   timestamp in milliseconds, clustered per entity
    col_3  VARCHAR            reference code
    col_4  VARCHAR            instance id
    col_5  VARCHAR            category
    col_6  VARCHAR NOT NULL   metric name, ten per category plus five shared
    col_7  VARCHAR            context, null in about 5 percent of the rows
    col_8  DOUBLE             value

The three code columns are generated as integer ids and rendered to strings
with a *shape* (DESIGN.md, axis A4): a constant prefix and a zero-padded
number, so that key equality and lexicographic order are the same in every
shape and only the byte length changes. Twelve bytes is the inline limit of
Arrow string views.

    shape  col_1 (entity)        col_3 (reference)      col_4 (instance)
    S0     E-00001        (7)    R-0001         (6)     I-0001         (6)
    S1     ENTITY-0000001 (14)   R-0001         (6)     I-0001         (6)
    S2     ENTITY-0000001 (14)   REFERENCE-0000001 (17) INSTANCE-00000001 (17)

Rows repeat on (col_1, col_2) because every entity draws its timestamps from a
few clusters of consecutive milliseconds; a small share repeats on all six
grouping columns by chance. `duplicate_share` (axis A6) adds deliberate
copies: with share s and n rows, the table holds about n * (1 - s) distinct
grouping keys; a copy keeps the six grouping columns of its original and draws
new col_7 and col_8. Everything derives from a seed and a fixed time origin.

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
NULL_CONTEXT_SHARE = 0.05

# shape -> (prefix, digits) for entity, reference, instance
SHAPES = {
    "S0": (("E-", 5), ("R-", 4), ("I-", 4)),
    "S1": (("ENTITY-", 7), ("R-", 4), ("I-", 4)),
    "S2": (("ENTITY-", 7), ("REFERENCE-", 7), ("INSTANCE-", 8)),
}
DEFAULT_SHAPE = "S1"

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
ID_SORT_KEY = [("entity", "ascending"), ("col_2", "ascending")]

LETTERS = np.array(list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))


def make_pools(rng, cluster_ms=CLUSTER_MS):
    """Value pools shared by every file of a dataset.

    The cluster bases leave room for a cluster of `cluster_ms` before the time origin.
    """
    categories = np.array([f"CAT_{i + 1}" for i in range(N_CATEGORIES)])
    metrics = [f"M{c + 1}_{i + 1:02d}" for c in range(N_CATEGORIES)
               for i in range(METRICS_PER_CATEGORY)]
    shared = [f"METRIC_{i + 1:02d}" for i in range(N_SHARED_METRICS)]
    cluster_base = rng.integers(ORIGIN_MS - SPAN_MS, ORIGIN_MS - cluster_ms, N_CLUSTERS)
    # Each entity samples its timestamps from 5 to 10 of the clusters.
    entity_clusters = [rng.choice(cluster_base, rng.integers(5, 11), replace=False)
                       for _ in range(N_ENTITIES)]
    return {
        "categories": categories,
        "metrics": np.array(metrics),
        "shared_metrics": np.array(shared),
        "contexts": np.array([f"CONTEXT_{letter}" for letter in LETTERS[:N_CONTEXTS]]),
        "entity_clusters": entity_clusters,
    }


def _contexts(rows, rng, pools):
    context = pools["contexts"][rng.integers(N_CONTEXTS, size=rows)].astype(object)
    context[rng.random(rows) < NULL_CONTEXT_SHARE] = None
    return context


def generate_rows(rows, rng, pools, cluster_ms=CLUSTER_MS):
    """`rows` rows with integer ids for the code columns, not sorted, not rendered.

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

    return pa.table({
        "entity": entity.astype(np.int64),
        "col_2": col_2,
        "reference": rng.integers(N_REFERENCES, size=rows).astype(np.int64),
        "instance": rng.integers(N_INSTANCES, size=rows).astype(np.int64),
        "col_5": pools["categories"][category],
        "col_6": col_6,
        "col_7": pa.array(_contexts(rows, rng, pools), type=pa.string()),
        "col_8": rng.uniform(-1000.0, 1000.0, size=rows),
    })


def add_duplicates(table, rows, rng, pools):
    """Grow `table` to `rows` rows by copying rows with new col_7 and col_8.

    Copies are drawn with replacement from the original rows, so a key may be
    copied more than once. Every row gets a stable id in `rid`; `origin` holds
    the rid of the original for copies and -1 for originals. Both survive any
    later sorting and are dropped by `render`.
    """
    n = table.num_rows
    extra = rows - n
    source = rng.integers(n, size=extra)
    copies = table.take(pa.array(source))
    copies = copies.set_column(copies.schema.get_field_index("col_7"), "col_7",
                               pa.array(_contexts(extra, rng, pools), type=pa.string()))
    copies = copies.set_column(copies.schema.get_field_index("col_8"), "col_8",
                               pa.array(rng.uniform(-1000.0, 1000.0, size=extra)))
    rid = np.arange(n + extra, dtype=np.int64)
    origin = np.concatenate([np.full(n, -1, dtype=np.int64), source.astype(np.int64)])
    return (pa.concat_tables([table, copies])
            .append_column("rid", pa.array(rid)).append_column("origin", pa.array(origin)))


def generate_table(rows, seed, cluster_ms=CLUSTER_MS, duplicate_share=0.0):
    """One table of `rows` rows from `seed`, not sorted, not rendered.

    With `duplicate_share` s > 0, about rows * (1 - s) rows are generated and
    the rest are copies (see `add_duplicates`); the table then carries the
    `rid` and `origin` columns.
    """
    rng = np.random.default_rng(seed)
    pools = make_pools(rng, cluster_ms)
    if duplicate_share <= 0:
        return generate_rows(rows, rng, pools, cluster_ms)
    base = generate_rows(int(round(rows * (1 - duplicate_share))), rng, pools, cluster_ms)
    return add_duplicates(base, rows, rng, pools)


def render(table, shape=DEFAULT_SHAPE):
    """Replace the integer ids with strings of the given shape; columns in schema order."""
    (ep, ed), (rp, rd), (ip, idg) = SHAPES[shape]

    def code(ids, prefix, digits):
        return pa.array([f"{prefix}{i:0{digits}d}" for i in ids.to_numpy()], type=pa.string())

    return pa.table({
        "col_1": code(table["entity"], ep, ed),
        "col_2": table["col_2"],
        "col_3": code(table["reference"], rp, rd),
        "col_4": code(table["instance"], ip, idg),
        "col_5": table["col_5"],
        "col_6": table["col_6"],
        "col_7": table["col_7"],
        "col_8": table["col_8"],
    }, schema=SCHEMA)


def distinct_keys(table):
    """Number of distinct values of the six grouping columns (ids or rendered)."""
    names = table.column_names
    cols = [c for c in ("entity", "col_1", "col_2", "reference", "col_3", "instance", "col_4",
                        "col_5", "col_6") if c in names]
    return table.select(cols).group_by(cols).aggregate([]).num_rows


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
    p.add_argument("--shape", choices=sorted(SHAPES), default=DEFAULT_SHAPE)
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
        table = render(generate_rows(args.rows, rng, pools, args.cluster_ms), args.shape)
        write_sorted(table, args.output_dir / f"reproducible_data_{i:0{width}d}.parquet")
    print(f"{args.output_dir}: {args.files} files of {args.rows} rows, seed {args.seed}, "
          f"shape {args.shape}, cluster width {args.cluster_ms} ms")


if __name__ == "__main__":
    main()
