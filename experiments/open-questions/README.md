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

## 2. The memory of the many-stream plan (partly explained)

Round 5: with 1196 ordered groups the process uses about 2 GB at every pool
size, against about 0.6 GB without order. Tests (`many-streams/`), 3 runs
each, 256 MB, ordered plan unless stated:

| variant | process RSS (MB) | elapsed (s) |
|---|---|---|
| dedup query, as in round 5 | 1452, 1503, 2148 | 0.60 to 0.66 |
| dedup query, strings as plain Utf8 | 1750, 2085, 1682 | 0.63 to 0.67 |
| dedup query, batch size 1024 instead of 8192 | 2109, 2115, 1573 | 0.73 to 0.75 |
| `ORDER BY` only, 1196 streams into the final merge | 784, 855, 824 | 0.22 |
| `ORDER BY` only, original plan (2 groups and a sort) | 346, 368, 349 | 0.22 |

The RSS is noisy, but neither the string representation nor the batch size
moves it: the memory is not data in transit. Without repartition and
aggregates, 1196 open streams cost about 0.45 GB over the original. With the
deduplication the excess is about 1.4 GB, so most of it appears above the
scan, where the plan has one partial aggregate per stream (1196 of them) and
an order-preserving repartition whose two outputs each merge 1196 inputs.
Which of these holds the memory was not attributed: there is no heap profiler
on this machine.

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
