# Open questions after round 5

Three points that the round-5 report listed as not measured, first examined on
2026-10-02 at the author's request, and a fourth, the open-file limit, added
on 2026-10-04. They are not part of the design of the top-level `PLAN.md`.
Points 2 and 3 have plans of their own, written and committed before their
first runs (`many-streams/PLAN.md`, `depth/PLAN.md`); point 1 and the first
probes of 2 and 3 are one-variable tests with few runs. Every SQL file,
output and stderr is in the subdirectories, and the dead ends are reported.

Everything here is run by `scripts/run_lab.sh`, after the matrix of the
round, and the figures below are from the run that accompanies round 7
(2026-10-04). The scripts refuse to overwrite earlier outputs: those of
2026-10-02 and of the run with round 6 are kept in the subdirectories
`2026-10-02/` and `2026-10-04-round-6/` of each experiment. The three runs
agree on the mechanisms; where a figure moved from one run to the next, the
text gives all three, because that spread is part of the result.

In short: the three costs the round left unexplained point at one operator,
the repartition, and two of them at its order-preserving mode. It counts
shared buffers once per fragment it sends, in both modes (point 1), holds
memory for every pair of input and output partition (point 2), and
serializes ordered groups that read disjoint key ranges at the same moment
(point 3). The many streams also need many open files (point 4).

## 1. Why wide strings bring spills: string views and repartition accounting (explained)

Round 5 showed that when `col_3` and `col_4` exceed 12 bytes (shape S2), the
ordered plan spills in the final aggregate at 128 MB and loses most of its
gain. Test (`string-view/`): the same S2 and S0 data at 128 MB, ordered plan,
with strings read as Arrow string views (the default) and as plain `Utf8`.
Plain `Utf8` needs two settings: `datafusion.execution.parquet.schema_force_view_types = false`
and `datafusion.sql_parser.map_string_types_to_utf8view = false`; the first
alone leaves the columns as `Utf8View`, because the table is declared with
`VARCHAR` and the SQL planner maps it to views (checked with `arrow_typeof`).

First probe, 2026-10-02 (ordered plan only; the test on both plans below repeats it):

| shape | strings | elapsed, 5 runs (s) | final-aggregate spills | partial agg out | repartition out |
|---|---|---|---|---|---|
| S0 | views | 0.333 to 0.367 | 0 | 136 MB | 64 MB |
| S0 | Utf8 | 0.360 to 0.405 | 0 | 87 MB | 45 MB |
| S2 | views | 0.608 to 0.695 | 14 to 22 | 371 MB | 652 MB |
| S2 | Utf8 | 0.385 to 0.409 | 0 | 126 MB | 61 MB |

With plain strings the wide shape behaves like the narrow one. The cost is in
the string-view representation, not in the length of the strings as such.

The mechanism, read in the source at the commit (DataFusion e1aa7d956,
arrow 60.0.0):

1. The hash repartition reorders every input batch with one `take_arrays`
   over the row indices grouped by output partition, and then hands each
   output a zero-copy `slice` of that one reordered batch
   (`repartition/mod.rs`, around lines 1337 to 1378).
2. Before sending a slice, the repartition reserves memory for it with
   `batch.get_array_memory_size()` (`repartition/mod.rs`,
   `OutputChannel::send`). That size is the full capacity of every buffer
   the slice refers to, whatever its offset and length. So every slice
   reserves the whole reordered batch, for every column and every string
   type: with M outputs the batch is counted up to M times. Plain `Utf8` and
   primitive columns are counted repeatedly too.
3. String views add a second level. `take` copies the 16-byte views and
   shares *all* the data buffers of the source array (`arrow-select`
   `take.rs`, `take_byte_view`: `Arc::clone(array.data_buffers())`), and a
   string-view array reports the capacity of all its data buffers plus its
   views (`arrow-array` `byte_view_array.rs`, `get_buffer_memory_size`). So
   every slice also counts the data buffers of the source batch, which hold
   the strings of all the rows, not only of the rows it carries. This is
   what makes the count explode with views and stay small without them.
4. DataFusion's own `RecordBatchMemoryCounter` (`common/src/utils/memory.rs`)
   documents this double counting and offers a counter that counts each
   buffer once; the repartition does not use it. The `output_bytes` metric
   uses the per-batch size too, which is why it grows tenfold.
5. A string of up to 12 bytes is stored inside its view and has no data
   buffer, so the second level has nothing to count. Above 12 bytes it has.
   This is the 12-byte threshold of round 5.

The accounting belongs to the hash repartition in general, not only to its
order-preserving mode: the original plan's repartition splits and reserves
the same way, which is why string views hurt the original plan too (below).

Inference, not measured: the inflated reservations of the repartition take
memory from the fair pool, and the final aggregate, which shares the pool,
reaches its limit sooner and spills. The test above shows that removing the
views removes the spills; it does not separate the repartition's share from
other consumers'.

### Both plans, string views on and off (`string-view-both/`)

The test above touched only the ordered plan. This one runs both plans, five
runs each after a warm-up, configurations interleaved. Median elapsed seconds
and median final-aggregate spills; `results.tsv` has every run.

| shape | pool | original, views | original, Utf8 | ordered, views | ordered, Utf8 |
|---|---|---|---|---|---|
| S0 | 128 MB | 0.633 (22) | 0.694 (20) | 0.342 (0) | 0.380 (0) |
| S0 | 512 MB | 0.586 (6) | 0.661 (6) | 0.368 (0) | 0.384 (0) |
| S2 | 128 MB | 0.770 (27) | 0.704 (22) | 0.650 (14) | 0.404 (0) |
| S2 | 512 MB | 0.700 (6) | 0.634 (6) | 0.400 (0) | 0.411 (0) |

Gain of the ordered plan over the original:

| shape | pool | with string views | with plain Utf8 |
|---|---|---|---|
| S0 | 128 MB | 46 % | 45 % |
| S0 | 512 MB | 37 % | 42 % |
| S2 | 128 MB | 16 % | 43 % |
| S2 | 512 MB | 43 % | 35 % |

What it shows:

- The collapse of the gain on wide strings at 128 MB belongs to the string
  views. With plain strings the ordered plan keeps 43 percent there. Over the
  three runs the gain with views was 12, 12 and 16 percent, with plain
  strings 43, 41 and 43.
- String views on wide strings hurt the original plan too: at 128 MB its sort
  spills 33 times instead of 8. In this run all five of its runs completed;
  in each of the two earlier runs one of five failed. The views hurt the
  ordered plan more, whose order-preserving repartition reports 592 MB of
  output against 328 for the original's; with plain strings it reports 61.
- Where the views hold no data buffer, they are not a cost: on S0 both
  plans are as fast or faster with them.
- At 512 MB the original plan is faster with plain strings on S2 (0.634
  against 0.700 s), which narrows the gain to 35 percent. Not investigated.

`summary.txt` and `results.tsv` in `string-view-both/` have every figure.

The conclusion of round 5 changes accordingly: the width of the strings does
not by itself decide whether the ordered plan spills. What decides is how the
engine accounts the memory of its string representation, and that is a
property of this version of DataFusion, not of the data.

## 2. The memory of the many-stream plan (partly explained)

With about 1200 ordered streams the deduplication query uses about 2 GB of
process RSS, against about 0.6 GB for the original plan.

### First probes (`many-streams/probes.py`, 3 runs each, 256 MB)

| variant | process RSS (MB) | elapsed (s) |
|---|---|---|
| dedup query, ordered plan | 2135, 2220, 1961 | 0.64 to 0.68 |
| dedup query, strings as plain Utf8 | 1982, 1933, 2118 | 0.62 to 0.65 |
| dedup query, batch size 1024 instead of 8192 | 2145, 1982, 1844 | 0.72 to 0.75 |
| `ORDER BY` only, 1196 streams into the final merge | 726, 714, 830 | 0.21 |
| `ORDER BY` only, original plan (2 groups and a sort) | 401, 429, 405 | 0.21 to 0.23 |

Neither the string representation nor the batch size moves the RSS. Without
repartition and aggregates the excess is about 0.35 GB; with the
deduplication about 1.5 GB.

### Four explanations, tested (`many-streams/PLAN.md`, written before the runs)

S, the scan holds a fixed amount per open stream; P, one partial aggregate per
stream; R, the order-preserving repartition holds memory per pair of input and
output partition; A, the allocator keeps freed memory. Datasets of 150, 300,
600 and 1200 files with total overlap, the same 600,000 rows; five runs per
ordered configuration and three per original baseline, interleaved; peak RSS
from `/usr/bin/time`, peak commit from mimalloc's statistics. Raw runs in
`results.tsv`, analysis in `analyze.py` and `analysis.txt`. No run failed.

**E1, excess RSS over the original plan regressed on the number of ordered
groups** (20 runs per query; 95 percent interval on the slope, taking the
baseline of each file count, the median of three original runs, as exact):

| query | columns read | slope per stream | 95 % interval | R² |
|---|---|---|---|---|
| Q1, `ORDER BY` only | 8 | 325 KB | 255 to 394 KB | 0.84 |
| Q2, `GROUP BY col_1, col_2` | 3 | 29 KB | -27 to 86 KB | 0.06 |
| Q3, deduplication | 8 | 1027 KB | 849 to 1205 KB | 0.89 |

A bootstrap that resamples both the ordered and the original runs of every
file count, so that the uncertainty of the baseline enters, gives 269 to 372
KB for Q1, 8 to 43 KB for Q2 and 924 to 1147 KB for Q3 (10,000 resamples;
with three baseline runs per point a rough interval). `analyze.py` leaves
failed runs out of every statistic; this series has none. The Q1 slope was
383 KB on 2026-10-02 and 418 KB in the run with round 6: over the three runs
it ranges from 0.33 to 0.42 MB per stream, more than any single interval
suggests.

**E2, Q3 at 1200 files, number of output partitions:**

| outputs | median RSS (MB) | runs (MB) | median peak commit (MB) |
|---|---|---|---|
| 2 | 2127 | 1895 to 2243 | 3072 |
| 4 | 2293 | 2141 to 2539 | 2867 |
| 8 | 3590 | 3483 to 3611 | 3994 |

**E3, 1200 files, mimalloc purge delay:**

| query | purge delay | median RSS (MB) | runs (MB) | median peak commit (MB) |
|---|---|---|---|---|
| Q1 | default | 790 | 707 to 847 | 571 |
| Q1 | 0 | 768 | 749 to 772 | 561 |
| Q3 | default | 2127 | 1895 to 2243 | 3072 |
| Q3 | 0 | 1725 | 1545 to 1924 | 2662 |

Verdicts:

- **S, supported; the coefficient is rough.** With the `ORDER BY`-only query
  the excess grows with the streams, about 0.33 MB per active read stream in
  this run (R² 0.84), 0.38 and 0.42 MB in the two earlier ones: 0.4 to 0.5 GB
  at 1196 streams. In this test every file is a stream of its own, so the
  cost is per stream, not per file. It is measured on a query that reads all
  eight columns and carried over to the deduplication, which reads the same
  eight; with three columns read (Q2) the slope is flat.
- **R, supported by scaling.** At a fixed 1196 inputs, going from 2 to 8
  outputs adds about 1.5 GB in this run and 1.7 to 1.8 GB in the earlier
  ones, that is between 0.20 and 0.25 MB for each additional pair of input
  and output partition. In the two earlier runs the point at 4 outputs lay
  on the same line; in this one it does not (2293 MB measured, about 2600
  predicted), because the median at 2 outputs fell on the upper of the two
  levels described below. Applied to the 2392 pairs at 2 outputs, the
  coefficient gives 0.5 to 0.6 GB; this assumes no fixed cost per input or
  per output, which the test does not identify, and the coefficient is
  derived from the increase, not confirmed independently of it. Raising the
  outputs also adds final aggregates and merges, but their number grows with
  the outputs only. A structural reason is expected: a merge keeps the
  current batch of every active input, and there is one merge per output.
- **P, not decided.** The test planned for it does not isolate it: Q2 reads 3
  columns instead of 8, so it differs from Q3 in the scan as well as in the
  aggregate state. Its flat slope suggests that the per-stream scan cost
  depends on the columns read (inference).
- **A, a part, of unknown size.** The RSS of the deduplication at 1196
  streams falls on one of two levels from run to run, about 1.8 and about
  2.1 GB, with the peak commit at 2458 or 3072 MB: the steps are the
  allocator's. Eager purging lowered the median RSS of Q3 in two of the three
  runs, by 150 and by 400 MB, and raised it by 109 MB in the first; the peak
  commit fell only in this run. For Q1 purging lowers the RSS by 20 to 70 MB.
  So memory the allocator retains is part of the excess; these runs do not
  say how much.

In short, 1196 ordered streams add between 1.2 and 1.5 GB to the
deduplication query, depending on the level the run falls on. Of that, 0.4 to
0.5 GB are the active read streams of the scan and 0.5 to 0.6 GB the
order-preserving repartition with its channel pairs. What remains ranges from
0.1 to 0.6 GB over the three runs: it is not a component the data can
resolve, and part of it is memory retained by the allocator. The
repartition's share grows with streams times outputs, so raising
`target_partitions` makes the many-stream plan more expensive in memory, not
cheaper. What exactly the repartition holds per pair (a buffered batch per
channel, the merge cursors, the spill pools) was not traced in the code.

## 3. Why depth 1 is slower than depth 2 (explained)

Same plan in both, two ordered groups, in the unmodified binary too. At depth 1
the process does about the same CPU work as at depth 2 and uses fewer cores
(`depth/probes.py`, means of 6 runs each, 256 MB):

| case | wall (s) | CPU seconds | cores used |
|---|---|---|---|
| depth 1, 12 files | 0.480 | 0.752 | 1.57 |
| depth 2, 12 files | 0.417 | 0.812 | 1.95 |
| depth 1, 120 files | 0.368 | 0.867 | 2.36 |
| depth 2, 120 files (3 groups) | 0.343 | 0.928 | 2.70 |
| depth 1, 12 files, files split by name | 0.513 | 0.745 | 1.45 |

The first probes rejected serialization because splitting the files by name
was not slower than interleaving them. That reasoning was wrong: at depth 1
the two partitions read disjoint key ranges at every moment in both splits,
so the test could not discriminate (see `depth/PLAN.md`).

### Three explanations, tested (`depth/PLAN.md`, written before the runs)

- **H1, serialization by backpressure, supported.** In the order-preserving
  repartition every input partition has its own channels, one per output,
  behind a gate that closes once all of them hold data
  (`repartition/distributor_channels.rs`). At depth 1 the merge in each
  output can take rows from only one input at a time, because the two inputs'
  current key ranges never overlap; the other input runs ahead by about one
  batch per output and waits. The batch size sets how far it can run ahead
  (means of 6 runs each):

  | batch size | depth 1 | depth 2 | gap in cores |
  |---|---|---|---|
  | 2048 | 0.543 s, 1.45 cores | 0.415 s, 1.92 cores | 0.48 |
  | 8192 (default) | 0.490 s, 1.56 cores | 0.418 s, 1.94 cores | 0.37 |
  | 32768 | 0.423 s, 1.99 cores | 0.417 s, 2.02 cores | 0.03 |
  | 131072 | 0.717 s, 2.12 cores (3 of 6 completed) | 0.760 s, 2.05 cores (3 of 6 completed) | -0.07 |

  The gap falls as the batches grow and closes at 32768 rows; depth 2 barely
  moves until the largest batch, where both slow down because the CPU work
  doubles. Six runs per cell after a warm-up, interleaved in rotating order,
  wall and CPU time of the whole process from `/usr/bin/time`;
  `depth/batch/run.py` writes every run to `results.tsv` and the means with
  their standard deviations to `summary.txt`. Standard deviation of the wall
  time up to 0.026 s, of the cores up to 0.07.

  At 131072 rows six of the twelve runs failed with `Resources exhausted` in
  `SortPreservingMergeExec[0]`; in the two earlier runs three and six did.
  The row above gives the means over the completed runs only. Larger batches
  close the gap in cores and are not a free remedy: at this size the query
  takes longer and often does not complete in 256 MB.
- **H2, unequal groups, not supported as the cause.** The imbalance is real:
  at depth 1 one group holds 60 percent of the rows (the generator gives the
  even files three entities and the odd ones two), at depth 2 the split is
  52/48. Its arithmetic even matches the observed ratio (0.60 against 0.52 of
  the work, 1.16; 0.48 against 0.42 s, 1.15). But at 32768 rows depth 1
  keeps the same imbalance and runs as fast as depth 2, so the imbalance does
  not produce the gap. The decision criterion written in the plan, "the rows
  per group differ more", was too weak: it checks that the imbalance exists,
  not that it acts.
- **H3, more and smaller batches, rejected.** The partial aggregate emits 106
  batches and the repartition 74 at both depths, in all ten round-5 runs.

One tension remains: with 120 files of about 5000 rows each, smaller than one
batch, the waiting input should be able to run a whole file ahead, yet the
gap in cores stayed (0.34). The 120-file depth-2 case had three groups instead
of two, so that comparison is confounded; it was not pursued.

What it means beyond this dataset: when the files of different ordered groups
cover disjoint key ranges at the same time, the order-preserving repartition
lets only one group advance at a time, and the parallelism of the scan is
lost; the more the groups' ranges overlap in time, the less this matters.
The default batch size of 8192 rows is in the range where the effect shows.

## 4. The open-file limit (measured)

A plan that keeps many ordered streams keeps many files open. Test
(`fd-limit/run.py`): the deduplication query on 1200 files with total overlap,
1196 ordered groups and two outputs, 256 MB, with the open-file limit of the
process set to four values. One run each; the question is whether the query
completes.

| open-file limit | original plan | ordered plan |
|---|---|---|
| 1024 | completes | fails, `Too many open files` |
| 4096 | completes | fails, `Too many open files` |
| 8192 | completes | completes |
| 16384 | completes | completes |

The original plan reads the 1200 files in two groups and completes under the
common default of 1024. The ordered plan opens every stream at once, and
1196 streams with two outputs need more than 4096 descriptors, more than
three per stream. Which descriptors are held at the failure was not listed;
in one run the open that failed was that of a temporary file, which suggests
that the spill files of the repartition, one per pair of input and output,
are part of the count (inference). The machine of the rounds has a limit of
1,048,576, which is why the matrix never met this failure.

