# Design

What the lab measures, how, and what was predicted. The outcomes are in
`RESULTS.md`, which `scripts/make_report.py` generates from the recorded
runs; this file holds no result.

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
| `original-target` | commit as is | `target_partitions` = the groups the overlap needs | the workaround: the order kept by raising the parallelism of the whole plan, scan and operators above it |
| `original-split-off` | commit as is | `target_partitions = 2`, `split_file_groups_by_statistics = false` | the setting a user starts from (the grouping by statistics is off by default); base case only |

The patch removes one check and is a measuring instrument, not a proposal.
`original-target` does not isolate the order: with four groups needed, both
`accept-groups` and `original-target` scan four ordered groups, but the
repartition and the final aggregate run on two partitions in the first and on
four in the second. It is the comparison with the workaround, not a control
for the order alone. It runs on every axis except A5, where the number of
groups is the thing being varied.

## Base case

- 12 files, overlap depth 4 (file *i* overlaps files *i+1* to *i+3*), 600,000
  rows in all, every file sorted by `(col_1, col_2)`; 4 ordered groups needed,
  `target_partitions = 2`, so `original` falls back to the unordered plan.
- String codes in shape S1: `col_1` above 12 bytes, `col_3` and `col_4` at
  most 12 bytes.
- Almost no duplicates on the six grouping columns.
- Fair memory pool of 256 MB.
- Query Q3, a deduplication: `GROUP BY` on six columns of which the first two
  are the sort key, `first_value` on the other two, `ORDER BY` the sort key.

## Axes of the matrix

Each axis varies one property while everything else stays at the base value.

**A1, the consumer of the order.** Four queries over the same table:

| query | shape | mode expected in the ordered plan | what the order can buy |
|---|---|---|---|
| Q1 | `SELECT * ... ORDER BY col_1, col_2` | no aggregate | a merge instead of a sort |
| Q2 | `GROUP BY col_1, col_2` with `count(*)`, `first_value(col_8)`, `ORDER BY col_1, col_2` | `Sorted` | completed groups can be emitted as the full grouping key changes |
| Q3 | the deduplication, `GROUP BY` six columns | `PartiallySorted([0, 1])` | completed groups can be emitted as the sort-key prefix changes |
| Q4 | `GROUP BY col_3, col_4, col_5, col_6` with `count(*)`, no `ORDER BY` | `Linear` | nothing in the aggregate |

**A2, the overlap depth:** 1, 2, 4, 12 ordered groups needed. At depth 1 and 2
the variants produce the same plan (control).

**A3, the memory budget:** fair pool of 128, 256, 512 MB.

**A4, the width of the strings.** Three shapes of the string codes: S0 all
three codes at most 12 bytes; S1 only `col_1` above (base); S2 `col_1`,
`col_3` and `col_4` above. The shapes are renderings of the same integer ids
with a constant prefix and a zero-padded number, so that the key equalities,
the positions of the duplicates, the lexicographic order, the overlap of the
file ranges, the row counts and the row-group boundaries are the same; only
the byte length of the strings changes. Twelve bytes is the inline limit of
Arrow string views.

**A5, the number of files for the same rows.** 12, 120 and 1200 files, in two
sub-series: depth 4 (the groups stay few, the files per group grow) and depth
equal to the number of files (every file overlaps every other, so the groups
equal the files). The groups the statistics produce are recorded in the
manifests next to the groups the construction intends.

**A6, the share of duplicates:** almost none (base) and one half, at the same
600,000 rows. Because `first_value` without an ordering is not deterministic
across plans, the comparison of the rows the variants return never uses the
values of `col_7` and `col_8`.

**Crossings:** A4 x A3 (the three shapes at the three pools) and A5 x A3 (the
total-overlap series at 1200 files at the three pools). No other crossing.

## Fixed in every run

The two binaries of `provenance/`, `datafusion-cli` with `--mem-pool-type
fair`, default batch size, zstd Parquet with statistics, datasets from fixed
seeds and a fixed time origin. One unrecorded warm-up, ten recorded runs,
variants in rotating order. Times are the `Elapsed` of `EXPLAIN ANALYZE`; per
operator, the spill count and bytes and the `output_bytes`; RSS from
`/usr/bin/time`. The plans are validated before timing, and the rows the
variants return are compared after it.

## Hypotheses on the matrix

- **H1 (A1).** The order buys the most for Q2 and Q3, where it changes the
  aggregate's mode; for Q1 it replaces one sort by one merge; for Q4 the
  aggregate does not use the order, and whatever difference remains between
  the variants is a scan effect, to be described.
- **H2 (A2).** At depth 1 and 2 the variants are identical. Above the target,
  the gain of `accept-groups` shrinks at depth 12, because the cost of the
  order-preserving repartition grows with the groups.
- **H3 (A3).** The gain grows with the pool; at 128 MB with the base data the
  ordered plan still wins on Q3.
- **H4 (A4 x A3).** Crossing the 12-byte limit on `col_3` and `col_4` brings
  spills to the ordered plan at 128 MB and shrinks its gain there; at 512 MB
  the ranking of the variants does not change.
- **H5 (A5 x A3).** With depth fixed at 4, going from 12 to 1200 files adds
  per-file costs to both variants alike, and the gap between them stays about
  what it is at 12 files. With depth equal to the file count, at 1200 files
  `accept-groups` loses at 128 MB and not at 512 MB.
- **H6 (A6).** No prediction on which plan profits more from duplicates.
- **H7 (`original-target`).** Its time is not predicted to equal that of
  `accept-groups`; whatever the differences are, they are reported as the
  cost or benefit of the workaround, not as a measure of the order.

The report checks each prediction with a rule it states.

## Experiments

One-variable tests on costs the matrix shows but does not explain. Each
script writes under `results/experiments/<name>/`.

**String views (`experiments/string-views/`).** A4 changes the width of the
strings; this changes their representation alone. The narrow and the wide
shape, 128 and 512 MB, both plans, strings read as Arrow string views (the
default) and as plain `Utf8`. Plain `Utf8` needs two settings:
`datafusion.execution.parquet.schema_force_view_types = false` and
`datafusion.sql_parser.map_string_types_to_utf8view = false`; the first alone
leaves the columns as views, because the table is declared with `VARCHAR` and
the SQL planner maps it. If the loss of the gain on wide strings belongs to
the representation, it disappears with plain strings.

The mechanism, read in the source at the commit (DataFusion `e1aa7d956`,
arrow 60.0.0), which the experiment does not instrument:

1. The hash repartition reorders every input batch with one `take_arrays`
   over the row indices grouped by output partition, and hands each output a
   zero-copy `slice` of that one reordered batch (`repartition/mod.rs`, around
   lines 1337 to 1378).
2. Before sending a slice it reserves memory for it with
   `batch.get_array_memory_size()` (`OutputChannel::send`), which is the full
   capacity of every buffer the slice refers to, whatever its offset and
   length. Every slice therefore reserves the whole reordered batch, for every
   column type.
3. String views add a second level: `take` copies the 16-byte views and
   shares all the data buffers of the source array (`arrow-select`
   `take_byte_view`), and a string-view array reports the capacity of all its
   data buffers. A string of up to 12 bytes is stored inside its view and has
   no data buffer.
4. `RecordBatchMemoryCounter` (`common/src/utils/memory.rs`) counts each
   buffer once; the repartition does not use it.

**The memory of many streams (`experiments/many-streams/`).** With about 1200
ordered streams the ordered plan uses much more process memory than the
original. Four explanations, not exclusive, each with a prediction:

- S, the scan: every active read stream holds a fixed amount. The excess RSS
  of the `ORDER BY` query, which has no repartition and no aggregate, grows
  linearly with the streams.
- P, the partial aggregates: one per stream. The slope of the deduplication
  exceeds that of the `ORDER BY` query, and a query with a small aggregate
  state lies between them.
- R, the order-preserving repartition: one channel per pair of input and
  output. At fixed streams the RSS grows with the output partitions.
- A, the allocator: memory freed and not yet returned. With eager purging
  (`MIMALLOC_PURGE_DELAY=0`) the peak RSS falls.

E1: 150, 300, 600 and 1200 files with total overlap, three queries, ordered
and original plan; the excess is regressed on the ordered groups. E2: the
deduplication at 1200 files with 2, 4 and 8 outputs. E3: eager purging. P
probes: plain `Utf8` strings, batches of 1024 rows. The peak RSS of this case
moves between runs, so the whole set is repeated in three independent series
and every coefficient is reported per series.

**Depth 1 against depth 2 (`experiments/depth/`).** With two ordered groups
the same plan is slower when their key ranges are disjoint. Three
explanations:

- backpressure: in the order-preserving repartition every input has its own
  channels behind a gate that closes once all of them hold data
  (`repartition/distributor_channels.rs`); with disjoint ranges the merge in
  each output takes rows from one input at a time and the other waits. The
  batch size sets how far the waiting input can run ahead, so with larger
  batches the gap in cores used shrinks;
- unequal groups: one group holds more rows and runs alone at the end. The
  imbalance does not depend on the batch size;
- more and smaller batches at depth 1: the batch counts of the partial
  aggregate and of the repartition differ between the depths.

Measured: four batch sizes at both depths; 120 files instead of 12; the files
split between the partitions by name. The report sets the batch-size series
beside the group shares from the manifests and the batch counts from the
matrix.

**The open-file limit (`experiments/open-files/`).** The many-stream case
with the limit on open files of the process set to several values, both
plans: which complete.

## Limits

One machine, one DataFusion commit, synthetic data, sub-second queries on a
few MB of Parquet, the fair pool only. The mechanisms are read in the source
and set beside the measurements; no run changes the accounting or
instruments the reservations.
