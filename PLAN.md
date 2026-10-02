# Plan for round 5: one base case, six axes, two declared crossings

Written on 2026-10-02, before any run of round 5, and revised the same day
after an external review (crossings declared, `original-target` reframed, Q4
and Q2 reworded, A4 and A6 made precise, A5 instrumented, names corrected).
One correction after the runs, on the data and not on the design: the
duplicate share of the base data (see the base case and A6).
Rounds 1 to 4 measured a single scenario, the deduplication query of the
issue, and round 4 showed that one unplanned property of the data (the width
of two string columns) changes the picture at 128 MB. Round 5 replaces the
single scenario with a base case and six axes, each varied on its own while
everything else stays at the base value, plus two crossings that the earlier
rounds showed to matter. The hypotheses below are written down before the
runs; the report states for each one whether the data supported it.

## Question

How does a query engine turn the order of sorted input files into ordered
execution, and what does it pay to keep that order? The engine is DataFusion
at commit `e1aa7d956` (version 55.1.0, 2026-09-28); the results describe that
version. The two plans compared are the one that drops the file order when
the ordered groups exceed `target_partitions` (`original`) and the one that
keeps it by accepting the extra groups (`accept-groups`).

## Variants

| variant | binary | settings | what it shows |
|---|---|---|---|
| `original` | commit as is | `target_partitions = 2`, `split_file_groups_by_statistics = true` | that version with statistics grouping on |
| `accept-groups` | commit plus `patch/accept-extra-groups.patch` | same | the order kept, with as many ordered groups as the overlap needs, downstream parallelism unchanged |
| `original-target` | commit as is | `target_partitions` = the groups the overlap needs | the workaround of the issue: the order kept by raising the parallelism of the whole plan, scan and operators above it |
| `original-split-off` | commit as is | `target_partitions = 2`, `split_file_groups_by_statistics = false` | the setting a user starts from (the grouping by statistics is off by default); base case only |

`original-target` does not isolate the order: with four groups needed, both
`accept-groups` and `original-target` scan four ordered groups, but the
repartition and the final aggregate run on two partitions in the first and on
four in the second. It is the comparison with the workaround, not a control
for the order alone. It runs on every axis except A5, where the number of
groups is the thing being varied. `original-split-off` runs on the base case
only, as a reference row; it is not "the defaults", because `target_partitions`
and the fair pool are set explicitly in every run.

## Base case

- 12 files, overlap depth 4 (file *i* overlaps files *i+1* to *i+3*), 600,000
  rows in all, every file sorted by `(col_1, col_2)`; 4 ordered groups needed,
  `target_partitions = 2`, so `original` falls back to the unordered plan.
- String codes in shape S1: `col_1` above 12 bytes, `col_3` and `col_4` at
  most 12 bytes. This is the shape of the reporter's data and the one where
  the ordered plan does not spill at 128 MB (`experiments/README.md`).
- Duplicates on the six grouping columns: 24 rows out of 600,000 (share
  0.00004); the base data is almost entirely distinct. The first version of
  this plan said "about 1.6 percent", which is the share in the reporter's
  data of rounds 1 to 4, not in this generator's.
- Fair memory pool of 256 MB.
- Query Q3, the deduplication of the issue: `GROUP BY` on six columns of which
  the first two are the sort key, `first_value` on the other two, `ORDER BY`
  the sort key.

## Axes

**A1, the consumer of the order.** Four queries over the same table:

| query | shape | mode expected in the ordered plan | what the order can buy |
|---|---|---|---|
| Q1 | `SELECT * ... ORDER BY col_1, col_2` | no aggregate | a merge instead of a sort |
| Q2 | `GROUP BY col_1, col_2` with `count(*)`, `first_value(col_8)`, `ORDER BY col_1, col_2` | `Sorted` | completed groups can be emitted as the full grouping key changes |
| Q3 | the deduplication query, `GROUP BY` six columns | `PartiallySorted([0, 1])` | completed groups can be emitted as the sort-key prefix changes |
| Q4 | `GROUP BY col_3, col_4, col_5, col_6` with `count(*)`, no `ORDER BY` | `Linear` | nothing in the aggregate |

Q4 checks whether changing the scan grouping has a measurable effect even when
the aggregate does not benefit from the input order: the two variants still
differ in the scan (four ordered groups of whole files against two groups of
byte ranges), so a difference is possible and is not attributed to the order.
The plans of both variants are compared first, to see what the optimizer
keeps different. The modes in the table are expectations; the plan check
before timing records the actual ones.

**A2, the overlap depth:** 1, 2, 4, 12 ordered groups needed. At depth 1 and 2
the variants produce the same plan (control).

**A3, the memory budget:** fair pool of 128, 256, 512 MB.

**A4, the width of the rows.** Three shapes of the string codes: S0 all three
codes at most 12 bytes; S1 only `col_1` above (base); S2 `col_1`, `col_3` and
`col_4` above (the shape of round 4). The shapes are renderings of the same
underlying integer ids with a constant prefix and a zero-padded number, so
that across S0, S1 and S2 the key equalities, the positions of the duplicates,
the lexicographic order, the overlap of the file ranges, the row counts and
the row-group boundaries are the same; only the byte length of the strings
changes. Twelve bytes is the inline limit of Arrow string views: a value of
up to 12 bytes lives in the 16-byte view, a longer one in a data buffer the
view points into, and DataFusion reads Parquet strings as views by default.
What crossing the limit does to the bytes per batch is measured per operator
(the `output_bytes` metric of the scan, the partial aggregate, the repartition
and the final aggregate are recorded for every run), not deduced from the
limit.

**A5, the number of files for the same rows.** 12, 120 and 1200 files of the
same 600,000 rows, in two sub-series: depth 4 (the groups stay 4, the files
per group grow) and depth equal to the number of files (every file overlaps
every other, so the groups equal the files). The second sub-series is the
1000-file case of the earlier rounds; the first separates "many files" from
"many ordered streams". Row assignment for A5 is by rank in the sorted
order, not by entity, and the groups the statistics produce are reported next
to the groups the construction intends, because a file that spans two
entities carries a looser bound than its rows. For every A5 dataset the
manifest records file sizes and row-group counts. The `Elapsed` of
`EXPLAIN ANALYZE` in the CLI starts before the logical plan is built and
covers the file listing and the statistics reads done when the scan is
created (`datafusion-cli/src/exec.rs`, `catalog-listing/src/table.rs`,
`list_files_for_scan`), so the per-file overhead of small files is inside the
measured time; the `CREATE EXTERNAL TABLE` statement is timed separately and
reported too.

**A6, the share of duplicates,** defined as
`duplicate_share = 1 - distinct_grouping_keys / total_rows`
over the six grouping columns of Q3. Two values: 0.00004 (base, 24 duplicate
rows) and 0.5, at the same 600,000 rows, so the 0.5 dataset has about 300,000
distinct keys; the axis compares almost no duplicates with half.
Copies of a key carry new values in `col_7` and `col_8`, and are assigned to
files like any other row, independently of the original, so a share of them
lands in a different file from their original; the manifest reports the share
of copies that fell in the same file as their original. Q3 only. Because
`first_value` without an ordering is not deterministic across plans, the
check between the variants is on row counts, never on the values of `col_7`
and `col_8`.

## Declared crossings

Two interactions that the earlier rounds showed to matter are measured on
purpose, and counted:

- **A4 × A3:** S0, S1 and S2 at 128, 256 and 512 MB (S1 at the three pools is
  already A3; adds 6 cases).
- **A5 × A3:** the depth-equal-to-files sub-series at 1200 files at 128 and
  512 MB (adds 2 cases).

No other crossing. The count: A1 4 + A2 4 + A3 3 + A4 3 + A5 6 + A6 2 = 22
cases less the base case counted in every axis, 17 distinct cases, plus 8 for
the crossings, 25 cases. Each case runs two or three variants, eleven runs
each. On this machine about 40 minutes in all; the 1200-file cases are the
slow ones.

## Fixed in every run

Commit `e1aa7d956`, the two binaries of round 3 (SHA-256 in
`results/round-4/binaries.sha256`), AMD Ryzen 9 7950X3D under Linux
7.0.0-34, `datafusion-cli` with `--mem-pool-type fair`, default batch size,
one Parquet row group per MiB of rows, zstd, statistics written. Datasets from
`scripts/generate_base.py` with fixed seeds and the fixed time origin. One
unrecorded warm-up, ten recorded runs, variants alternated. Times are the
`Elapsed` of `EXPLAIN ANALYZE`; per operator, the spill count and the spilled
bytes of the sort, the partial aggregate, the final aggregate and the
repartition, and the `output_bytes`; RSS from `/usr/bin/time`. The report
compares medians with the first and third quartile; min and max stay in the
per-run TSVs. The count of completed runs is stated whenever it is below ten.

## Hypotheses, written before the runs

- **H1 (A1).** The order buys the most for Q2 and Q3, where it changes the
  aggregate's mode; for Q1 it replaces one sort by one merge and the gain is
  bounded by the cost of that sort; for Q4 the aggregate does not use the
  order, and whatever difference remains between the variants is a scan
  effect, to be described, not explained away.
- **H2 (A2).** At depth 1 and 2 the variants are identical. Above the target,
  the gain of `accept-groups` is largest at depth 4 and shrinks at depth 12,
  because the cost of the order-preserving repartition grows with the groups.
- **H3 (A3).** The gain grows with the pool as long as the ordered plan's
  spills disappear and the unordered plan's do not; at 128 MB with the base
  data the ordered plan still wins on Q3, because its strings are below the
  inline limit.
- **H4 (A4 × A3).** Crossing the 12-byte limit on `col_3` and `col_4` raises
  the bytes per batch above the scan, brings spills to the ordered plan at
  128 MB and shrinks its gain there; at 512 MB the ranking of the variants
  does not change. S0 against S1 shows the same effect for `col_1`, smaller.
  How much the bytes rise, and in which operator, is what the metrics say;
  "about twice" is the figure of round 4 and not part of the hypothesis.
- **H5 (A5 × A3).** With depth fixed at 4, going from 12 to 1200 files adds
  per-file costs (opens, footers, metadata, smaller batches) to both variants
  alike, and the gap between them stays about what it is at 12 files. With
  depth equal to the file count, the cost of `accept-groups` in RSS and
  descriptors grows with the files and at 1200 it loses at 128 MB, as in the
  earlier rounds, and not at 512 MB.
- **H6 (A6).** No prediction on the direction. With half the rows duplicated
  the final aggregate emits half the rows; whether the ordered plan or the
  hash plan profits more from that is what the axis measures.
- **H7 (`original-target`).** On Q3 at the base case `original-target` has
  the same operator kinds as `accept-groups` but a different distribution of
  the work (four partitions above the scan instead of two) and a time that is
  not predicted to be equal; on Q4 the extra partitions change the work
  without the order being used, and on Q1 the order is used by the merge in
  both. Whatever the differences are, they are reported as the cost or
  benefit of the workaround, not as a measure of the order.

## Stopping rule

One round. No axis is added after seeing the results. If a hypothesis fails
in a way that one further one-variable test can settle, that test may be run
and is reported under `experiments/`, as the two of 2026-10-02 are; at most
two such tests. Results that do not support a hypothesis are reported as
such, next to the hypothesis.

## What the report has to contain

One table per axis and one per crossing, with the base case as the first
row, every variant as a column group (median, first and third quartile;
completed runs if below ten), the spill counts and bytes of the sort, the
partial aggregate, the final aggregate and the repartition, the
`output_bytes` of the operators where A4 needs them, and the RSS. A line per
hypothesis: supported, not supported, or not decided, with the rows that
decide it. The list of what was not measured.

## For the article

Three passages stay at the centre: who uses the order (A1), how the layout
determines the streams (A2, A5), and how memory and concurrency change the
cost (A3 and the crossings). The other measurements explain those passages;
they do not each become a section.
