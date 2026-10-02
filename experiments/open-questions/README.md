# Open questions after round 5

Follow-up of 2026-10-02 on the three points that `RESULTS.md` lists as not
measured, run at the author's request after the round. These are not part of
the pre-registered design of `PLAN.md`; they are one-variable tests on the
round-5 datasets with the round-5 binaries, few runs each, reported with the
dead ends. Every SQL file, output and stderr is in the subdirectories.

## 1. Why wide strings bring spills: string views and repartition accounting (explained)

Round 5 showed that when `col_3` and `col_4` exceed 12 bytes (shape S2), the
ordered plan spills in the final aggregate at 128 MB and loses most of its
gain. Test (`string-view/`): the same S2 and S0 data at 128 MB, ordered plan,
with strings read as Arrow string views (the default) and as plain `Utf8`.
Plain `Utf8` needs two settings: `datafusion.execution.parquet.schema_force_view_types = false`
and `datafusion.sql_parser.map_string_types_to_utf8view = false`; the first
alone leaves the columns as `Utf8View`, because the table is declared with
`VARCHAR` and the SQL planner maps it to views (checked with `arrow_typeof`).

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

1. The hash repartition splits every input batch among its output partitions
   with `take_arrays` (`repartition/mod.rs`, around line 1368).
2. For string views, `take` copies the 16-byte views and shares *all* the data
   buffers of the source array (`arrow-select` `take.rs`, `take_byte_view`:
   `Arc::clone(array.data_buffers())`).
3. A string-view array reports as its memory the capacity of all its data
   buffers plus its views (`arrow-array` `byte_view_array.rs`,
   `get_buffer_memory_size`).
4. Before sending a batch, the repartition reserves memory for it with
   `batch.get_array_memory_size()` (`repartition/mod.rs`, `OutputChannel::send`),
   batch by batch, so every output fragment counts again the data buffers it
   shares with the others. DataFusion's own `RecordBatchMemoryCounter`
   (`common/src/utils/memory.rs`) documents exactly this double counting and
   offers a counter that counts each buffer once; the repartition does not use
   it. The `output_bytes` metric uses the per-batch size too, which is why it
   grows tenfold.
5. A string of up to 12 bytes is stored inside its view and has no data
   buffer, so there is nothing to count twice. Above 12 bytes there is. This
   is the 12-byte threshold of round 5.

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
| S0 | 128 MB | 0.649 (22) | 0.729 (19) | 0.365 (0) | 0.380 (0) |
| S0 | 512 MB | 0.609 (6) | 0.655 (6) | 0.339 (0) | 0.384 (0) |
| S2 | 128 MB | 0.756 (28), 4/5 completed | 0.708 (22) | 0.668 (14) | 0.405 (0) |
| S2 | 512 MB | 0.686 (6) | 0.619 (6) | 0.417 (0) | 0.425 (0) |

Gain of the ordered plan over the original:

| shape | pool | with string views | with plain Utf8 |
|---|---|---|---|
| S0 | 128 MB | 44 % | 48 % |
| S0 | 512 MB | 44 % | 41 % |
| S2 | 128 MB | 12 % | 43 % |
| S2 | 512 MB | 39 % | 31 % |

What it shows:

- The collapse of the gain on wide strings at 128 MB belongs to the string
  views. With plain strings the ordered plan keeps 43 percent there.
- String views on wide strings hurt the original plan too: at 128 MB its sort
  spills 34 times instead of 8 and one run of five failed. They hurt the
  ordered plan more, whose order-preserving repartition reports 592 MB of
  output against 325 for the original's.
- Where nothing is counted twice, string views are not a cost: on S0 both
  plans are as fast or faster with them.
- At 512 MB the original plan is faster with plain strings on S2 (0.619
  against 0.686 s), which narrows the gain to 31 percent. Not investigated.

The conclusion of round 5 changes accordingly: the width of the strings does
not by itself decide whether the ordered plan spills. What decides is how the
engine accounts the memory of its string representation, and that is a
property of this version of DataFusion, not of the data.

## 2. The memory of the many-stream plan (mostly explained)

Round 5: with about 1200 ordered streams the deduplication query uses about
2 GB of process RSS, against about 0.6 GB for the original plan.

### First probes (`many-streams/`, 3 runs each, 256 MB, ordered plan)

| variant | process RSS (MB) | elapsed (s) |
|---|---|---|
| dedup query, as in round 5 | 1452, 1503, 2148 | 0.60 to 0.66 |
| dedup query, strings as plain Utf8 | 1750, 2085, 1682 | 0.63 to 0.67 |
| dedup query, batch size 1024 instead of 8192 | 2109, 2115, 1573 | 0.73 to 0.75 |
| `ORDER BY` only, 1196 streams into the final merge | 784, 855, 824 | 0.22 |
| `ORDER BY` only, original plan (2 groups and a sort) | 346, 368, 349 | 0.22 |

Neither the string representation nor the batch size moves the RSS. Without
repartition and aggregates the excess is about 0.45 GB; with the
deduplication about 1.4 GB.

### Four explanations, tested (`many-streams/PLAN.md`, written before the runs)

S, the scan holds a fixed amount per open stream; P, one partial aggregate per
stream; R, the order-preserving repartition holds memory per pair of input and
output partition; A, the allocator keeps freed memory. Datasets of 150, 300,
600 and 1200 files with total overlap, the same 600,000 rows; five runs per
ordered configuration and three per original baseline, interleaved; peak RSS
from `/usr/bin/time`, peak commit from mimalloc's statistics. Raw runs in
`results.tsv`, analysis in `analyze.py` and `analysis.txt`. No run failed.

**E1, excess RSS over the original plan regressed on the number of ordered
groups** (20 runs per query; 95 percent interval on the slope):

| query | columns read | slope per stream | 95 % interval | R² |
|---|---|---|---|---|
| Q1, `ORDER BY` only | 8 | 383 KB | 330 to 437 KB | 0.93 |
| Q2, `GROUP BY col_1, col_2` | 3 | -15 KB | -63 to 34 KB | 0.02 |
| Q3, deduplication | 8 | 973 KB | 767 to 1179 KB | 0.85 |

**E2, Q3 at 1200 files, number of output partitions:**

| outputs | median RSS (MB) | runs (MB) | median peak commit (MB) |
|---|---|---|---|
| 2 | 1843 | 1755 to 2173 | 2458 |
| 4 | 2451 | 2217 to 2458 | 3277 |
| 8 | 3540 | 3459 to 3626 | 3994 |

**E3, 1200 files, mimalloc purge delay:**

| query | purge delay | median RSS (MB) | median peak commit (MB) |
|---|---|---|---|
| Q1 | default | 811 | 586 |
| Q1 | 0 | 743 | 580 |
| Q3 | default | 1843 | 2458 |
| Q3 | 0 | 1952 | 3072 |

Verdicts:

- **S, supported.** With the `ORDER BY`-only query the excess grows by about
  0.38 MB per open stream, linearly (R² 0.93, intercept about 17 MB): about
  0.45 GB at 1196 streams.
- **R, supported, and the largest part.** At a fixed 1196 inputs, going from 2
  to 8 outputs adds about 1.7 GB, roughly 0.24 MB for each additional pair of
  input and output partition; at 2 outputs that is about 0.57 GB. Raising the
  outputs also adds final aggregates and merges, but their number grows with
  the outputs only, while the measured increase matches the number of pairs.
- **P, not decided.** The test planned for it does not isolate it: Q2 reads 3
  columns instead of 8, so it differs from Q3 in the scan as well as in the
  aggregate state. Its flat slope suggests that the per-stream scan cost
  depends on the columns read (inference). After S and R, about 0.2 GB of the
  1.2 GB excess of Q3 at 1196 streams remains unattributed; that is the most
  the partial aggregates can account for.
- **A, not supported as a major part.** Eager purging lowers the RSS of Q1 by
  about 70 MB and does not lower that of Q3; the peak commit does not fall.
  The run-to-run spread of Q3 is bimodal in both RSS and peak commit (commit
  2355 or 3072 MB), which points at the allocator's commit steps rather than
  at the plan; not investigated further.

In short, of the roughly 1.2 GB that 1196 ordered streams add to the
deduplication query: about 0.45 GB are the open streams of the scan, about
0.57 GB the order-preserving repartition with its channel pairs, and about
0.2 GB remain unattributed. The repartition's share grows with streams times
outputs, so raising `target_partitions` makes the many-stream plan more
expensive in memory, not cheaper. What exactly the repartition holds per pair
(a buffered batch per channel, the merge cursors, the spill pools) was not
traced in the code.

## 3. Why depth 1 is slower than depth 2 (not explained)

Same plan in both, two ordered groups. Tests (`depth/`), 6 runs each, 256 MB:

| case | wall (s) | CPU seconds | cores used |
|---|---|---|---|
| depth 1, 12 files | 0.502 | 0.777 | 1.55 |
| depth 2, 12 files | 0.428 | 0.822 | 1.92 |
| depth 1, 120 files | 0.388 | 0.898 | 2.31 |
| depth 2, 120 files (3 groups) | 0.352 | 0.950 | 2.70 |
| depth 1, 12 files, files split by name | 0.517 | 0.760 | 1.47 |
| depth 1, 12 files, files split by statistics | 0.500 | 0.788 | 1.58 |

Measured: at depth 1 the process does about the same CPU work and uses fewer
cores; the repartition reports more time waiting to send (`send_time` 524
against 387 ms in round 5). Two explanations were tested and failed:

- *Coarse alternation.* At depth 1 the two groups cover disjoint key ranges in
  alternation, so the merge might take them in turns. With 120 smaller files
  the gap in cores stays (0.37 at 12 files, 0.39 at 120).
- *Serialization of disjoint partitions.* Splitting the files by name, so
  that one partition holds the first half of the keys and the other the
  second, should then be much slower. It is about the same (0.517 against
  0.500 s).

The cause of the lower parallelism at depth 1 remains open.
