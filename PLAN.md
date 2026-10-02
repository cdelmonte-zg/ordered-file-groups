# Plan for round 5: one base case, six axes

Written on 2026-10-02, before any run of round 5. Rounds 1 to 4 measured a
single scenario, the deduplication query of the issue, and round 4 showed that
one unplanned property of the data (the width of two string columns) changes
the picture at 128 MB. Round 5 replaces the single scenario with a base case
and six axes, each varied on its own while everything else stays at the base
value. No cross product. The hypotheses below are written down before the
runs; the report states for each one whether the data supported it.

## Question

How does a query engine turn the order of sorted input files into ordered
execution, and what does it pay to keep that order? The engine is DataFusion
at commit `e1aa7d956`; the two plans compared are the one that drops the file
order when the ordered groups exceed `target_partitions` (`original`) and the
one that keeps it by accepting the extra groups (`accept-groups`).

## Variants

| variant | binary | settings | what it isolates |
|---|---|---|---|
| `original` | commit as is | `target_partitions = 2`, `split_file_groups_by_statistics = true` | the engine today, with statistics grouping on |
| `accept-groups` | commit plus `patch/accept-extra-groups.patch` | same | the order kept, with as many ordered groups as the overlap needs |
| `original-target` | commit as is | `target_partitions` = the groups the overlap needs | the workaround of the issue: order kept by raising the parallelism; separates "ordered streams" from "more partitions" |
| `original-default` | commit as is | `target_partitions = 2`, `split_file_groups_by_statistics = false` | DataFusion's defaults, where a user starts; base case only |

`original-target` runs on every axis except A5, where the number of groups is
the thing being varied. `original-default` runs on the base case only, as a
reference row.

## Base case

- 12 files, overlap depth 4 (file *i* overlaps files *i+1* to *i+3*), 600,000
  rows in all, every file sorted by `(col_1, col_2)`; 4 ordered groups needed,
  `target_partitions = 2`, so `original` falls back to the unordered plan.
- String codes: `col_1` above 12 bytes, `col_3` and `col_4` at most 12 bytes.
  This is the shape of the reporter's data and the one where the ordered plan
  does not spill at 128 MB (`experiments/README.md`).
- Natural share of duplicates on the six grouping columns, about 1.6 percent.
- Fair memory pool of 256 MB.
- Query Q3, the deduplication of the issue: `GROUP BY` on six columns of which
  the first two are the sort key, `first_value` on the other two, `ORDER BY`
  the sort key.

## Axes

**A1, the consumer of the order.** Four queries over the same table:

| query | shape | mode expected in the ordered plan | what the order can buy |
|---|---|---|---|
| Q1 | `SELECT * ... ORDER BY col_1, col_2` | no aggregate | a merge instead of a sort |
| Q2 | `GROUP BY col_1, col_2` with `count(*)`, `first_value(col_8)`, `ORDER BY col_1, col_2` | `Sorted` | full streaming aggregation, no hash table |
| Q3 | the deduplication query, `GROUP BY` six columns | `PartiallySorted([0, 1])` | early emission per sort-key prefix |
| Q4 | `GROUP BY col_3, col_4, col_5, col_6` with `count(*)`, no `ORDER BY` | `Linear` | nothing: the order is not used downstream |

Q4 is the control: if `accept-groups` differs from `original` on Q4, the
difference comes from the number of partitions, not from the order.

**A2, the overlap depth:** 1, 2, 4, 12 ordered groups needed. At depth 1 and 2
the variants produce the same plan (control).

**A3, the memory budget:** fair pool of 128, 256, 512 MB.

**A4, the width of the rows.** Three shapes of the string codes, with the same
cardinalities: S0 all three codes at most 12 bytes; S1 only `col_1` above
(base); S2 `col_1`, `col_3` and `col_4` above (the shape of round 4). Twelve
bytes is the inline limit of Arrow string views, which is how DataFusion reads
Parquet strings by default.

**A5, the number of files for the same rows.** 12, 120 and 1200 files of the
same 600,000 rows, in two sub-series: depth 4 (the groups stay 4, the files per
group grow) and depth equal to the number of files (every file overlaps every
other, so the groups equal the files). The second sub-series is the 1000-file
case of the earlier rounds; the first separates "many files" from "many
ordered streams". Row assignment for A5 is by rank in the sorted order, not by
entity, and the groups the statistics produce are reported next to the groups
the construction intends, because a file that spans two entities carries a
looser bound than its rows.

**A6, the share of duplicates:** about 1.6 percent (base) and 50 percent, Q3
only. The 50 percent variant duplicates half of the base rows with new values
in `col_7` and `col_8`.

That is 4 + 4 + 3 + 3 + 6 + 2 = 22 cases less the overlaps with the base case,
about 18 distinct cases, times two to three variants, times eleven runs. On
this machine about 25 minutes per memory budget; A3 is the only axis that
multiplies by the pool size.

## Fixed in every run

Commit `e1aa7d956`, the two binaries of round 3 (SHA-256 in
`results/round-4/binaries.sha256`), AMD Ryzen 9 7950X3D under Linux
7.0.0-34, `datafusion-cli` with `--mem-pool-type fair`, default batch size,
one Parquet row group per MiB of rows, zstd, statistics written. Datasets from
`scripts/generate_base.py` with fixed seeds and the fixed time origin. One
unrecorded warm-up, ten recorded runs, variants alternated. Times are the
`Elapsed` of `EXPLAIN ANALYZE`, spills per operator from its metrics, RSS from
`/usr/bin/time`. Medians over completed runs, with the count of completed runs
stated whenever it is below ten.

## Hypotheses, written before the runs

- **H1 (A1).** The order buys the most for Q2 and Q3, where it changes the
  aggregate's mode; for Q1 it replaces one sort by one merge and the gain is
  bounded by the cost of that sort; for Q4 the variants agree within noise.
- **H2 (A2).** At depth 1 and 2 the variants are identical. Above the target,
  the gain of `accept-groups` is largest at depth 4 and shrinks at depth 12,
  because the cost of the order-preserving repartition grows with the groups.
- **H3 (A3).** The gain grows with the pool as long as the ordered plan's
  spills disappear and the unordered plan's do not; at 128 MB with the base
  data the ordered plan still wins on Q3, because its strings are below the
  inline limit.
- **H4 (A4).** Crossing the 12-byte limit on `col_3` and `col_4` roughly
  doubles the bytes per batch above the scan, brings spills to the ordered
  plan at 128 MB and shrinks its gain there; at 512 MB the ranking of the
  variants does not change. S0 against S1 shows the same effect for `col_1`,
  smaller.
- **H5 (A5).** With depth fixed at 4, going from 12 to 1200 files costs both
  variants little, because the number of ordered streams does not change.
  With depth equal to the file count, the cost of `accept-groups` in RSS and
  descriptors grows with the files and at 1200 it loses at 128 MB, as in the
  earlier rounds.
- **H6 (A6).** No prediction on the direction. With 50 percent duplicates the
  final aggregate emits half the rows; whether the ordered plan or the hash
  plan profits more from that is what the axis measures.
- **H7 (`original-target`).** Raising `target_partitions` to the needed
  groups gives the same plan shape as `accept-groups` and about the same
  time on Q3 at the base case, and a different time on Q1 and Q4, where the
  extra partitions change the work without the order being used.

## Stopping rule

One round. No axis is added after seeing the results. If a hypothesis fails
in a way that one further one-variable test can settle, that test may be run
and is reported under `experiments/`, as the two of 2026-10-02 are; at most
two such tests. Results that do not support a hypothesis are reported as
such, next to the hypothesis.

## What the report has to contain

One table per axis, with the base case as its first row, every variant as a
column group (median, min, max; completed runs if below ten), the spills of
the sort, the final aggregate and the repartition, and the RSS. A line per
hypothesis: supported, not supported, or not decided, with the rows that
decide it. The list of what was not measured.
