# Ordered file groups in DataFusion

*Where the order of sorted files is lost, what keeping it changes, and where it stops paying.*

## 1. Where the order is lost

Sorted Parquet files do not automatically produce a sorted scan. Concatenating two files preserves an ascending order only when their boundary values permit it. Overlapping files need separate streams, which can then be merged.

A file scan in DataFusion has one output partition for each file group, and reads the files of a group one after the next. An ordered group therefore becomes one ordered stream of the scan. In the path studied here, DataFusion builds ordered groups from the statistics of the files; the setting `split_file_groups_by_statistics` enables this statistics grouping. It starts with as many empty groups as `target_partitions`, the number of partitions the engine is configured to use, and considers the files by increasing minimum: a file is appended to the least populated group that is empty or whose last file's maximum is strictly below the file's minimum, and when no group qualifies another one is added. The statistics establish whether files can be concatenated; they do not prove that the rows within each file are sorted. The lab declares an order which its generator actually writes.

At DataFusion commit `e1aa7d956`, the listing-table code accepts the proposed groups only when their count does not exceed `target_partitions`. This text calls that check the guard. If the guard rejects the groups, the code retains the initial grouping; a later optimizer step can redistribute files by size and split them into byte ranges, and the resulting scan may no longer advertise an order. The engine reports the reason for the rejection in a log line at debug level; the plans the lab records do not show it.

The placement of a file is strict. Two files that touch at a boundary, the maximum of one equal to the minimum of the other, go to different groups, while `MinMaxStatistics::is_sorted` in the same engine accepts them (`max <= next_min`). In one of the lab's datasets, with 1,200 files, 248 pairs touch and the grouping produces 5 groups where 4 would do.

The practical question is whether the downstream work saved by order outweighs the resources needed to carry that order through the plan. Time and memory are separate outcomes: a faster plan can be more expensive to keep resident.

## 2. What the lab runs and how it measures

The base dataset is a table of eight columns with 600,000 rows in twelve files. Every file is sorted by `(col_1, col_2)` and overlaps the next three, so the bounds require four ordered groups. The configured `target_partitions` is two, and the guard decides the plan. Five queries run on the table:

| Query | What it does |
|---|---|
| Q1 | `SELECT *` with `ORDER BY col_1, col_2` |
| Q2 | `GROUP BY col_1, col_2`, the complete sort key, with the same `ORDER BY` |
| Q3 | A deduplication: `GROUP BY` six columns that begin with the sort key, with the same `ORDER BY` |
| Q4 | `GROUP BY col_3, col_4, col_5, col_6`, without the sort key and without `ORDER BY` |
| Q5 | Q3 without its `ORDER BY` |

Q3 is the main query of the lab:

```sql
SELECT col_1, col_2, col_3, col_4, col_5, col_6,
       first_value(col_7) AS col_7,
       first_value(col_8) AS col_8
FROM example
GROUP BY col_1, col_2, col_3, col_4, col_5, col_6
ORDER BY col_1 ASC, col_2 ASC;
```

Its input is ordered by a prefix of the grouping key, the pair `(col_1, col_2)`. DataFusion runs such an aggregation in two stages with a repartition between them: a partial aggregate on each partition of the scan, a repartition by the grouping key, and a final aggregate.

Four variants run the queries:

| Variant | Intervention | Purpose |
|---|---|---|
| `original` | Unmodified commit, statistics grouping enabled, target two | Observe the rejected grouping and subsequent plan |
| `accept-groups` | Remove the guard, keep target two | Measure the whole effect of accepting extra ordered groups |
| `original-target` | Original build, target raised to the required count | Compare the existing configuration workaround |
| `original-split-off` | Original build, statistics grouping disabled | Base-case reference for the grouping setting |

In the rest of the text the original plan is that of `original`, the ordered plan that of `accept-groups`, and the workaround is `original-target`. Removing the guard is a small change in the source and a broad one in execution: it changes the streams of the scan, the mode of the aggregates, which says whether they use the order of their input, the repartition and the presence of a sort. What the lab measures is the combined consequence of those changes.

The base case is Q3 on the base dataset with a target of two. The base layout is the way the files of that dataset overlap, and the lab reuses it with more rows and with more files.

The lab runs on one machine, a Ryzen 9 7950X3D, with the measured processes pinned to eight physical cores. Its main set of runs, the matrix, varies one property of the base case at a time, under DataFusion's fair memory pool at 128, 256 and 512 MB, and each configuration has a warm-up and ten recorded runs. The matrix records 620 executions, 620 of which complete. A separate check compares the results of the variants on their deterministic columns and passes 62 of 62 cells.

The report of the lab, `RESULTS.md`, calls one variant faster when its third quartile lies below the other's first. This is a declared descriptive rule, not a hypothesis test or a guarantee of statistical significance. For configurations with failures the report gives the completed runs beside the time of those that complete.

`DESIGN.md`, the design of the experiments, states the predictions, and the report checks each one against a declared rule; not all of them hold.

Four measures recur. Elapsed time is the `Elapsed` that `datafusion-cli` prints for the measured statement, planning included. Operator metrics are accumulated durations: `elapsed_compute` times the computation of an operator, and the `send_time` of a repartition includes delivery and waiting. A reservation is what an operator has declared to the memory pool, the engine's account of memory under a limit; the limit applies to reservations. RSS is the resident memory of the process, as the operating system measures it.

## 3. In the base layout, keeping the order pays where the unordered aggregation spills

In the ordered plan of Q3, a change of the prefix `(col_1, col_2)` proves that the previous prefix will not return in that stream, so the aggregate can finish those groups early.

At 256 MB, Q3 takes 0.603 seconds in the original plan and 0.351 in the ordered one, a reduction of 42 percent. With statistics grouping disabled the original build takes 0.603 seconds, within the quartiles of the original, so the setting alone does not move the plan. The original spills in its sort and in its final aggregate, which write part of what they hold to disk when the pool refuses them more memory; the ordered plan does not spill. A spill count says neither how much data moved nor how its work overlaps with other work.

| Metric, accumulated seconds | Original | Ordered |
|---|---:|---:|
| Sort `elapsed_compute` | 0.030 | absent |
| Final aggregate `elapsed_compute` | 0.888 | 0.378 |
| Partial aggregate `elapsed_compute` | 0.275 | 0.316 |
| Repartition `send_time` | 0.233 | 0.815 |

The largest reduction in recorded compute duration is at the final aggregate, while the repartition spends longer delivering batches in the ordered plan.

### A gain remains without a sort to remove

Q5 is the same deduplication without the final `ORDER BY`. Neither plan sorts, and the ordered plan still takes 0.306 seconds against 0.567. Together with the operator metrics, this supports an important role for the aggregation, with the changes in the scan and in the repartition coupled to it.

### Which queries use the order

Three other queries use the order in different ways.

| Query | Original seconds | Ordered seconds | What the comparison establishes |
|---|---:|---:|---|
| Q1, only `ORDER BY` | 0.049 | 0.027 | An ordered scan and merge can beat the scan-and-sort plan |
| Q2, group on the complete sort key | 0.050 | 0.052 | `Sorted` mode does not ensure a time benefit when the unordered alternative fits in memory |
| Q4, group without the sort key | 0.014 | 0.014 | The optimizer discards the unused ordering; the measured times overlap |

### With enough memory the advantage disappears

Up to 512 MB, the largest pool of the matrix, the final aggregate of the original plan spills at every pool and that of the ordered plan at none. Under larger pools the base case changes.

| Pool | Original seconds | Ordered seconds | Original final-aggregate spills, median |
|---|---:|---:|---:|
| 512 MB | 0.633 | 0.350 | 6 |
| 1 GB | 0.611 | 0.356 | 4 |
| 2 GB | 0.362 | 0.357 | 0 |
| 4 GB | 0.346 | 0.357 | 0 |

From the pool `2g` up, the final aggregate of the original no longer spills in any run, and the advantage of the ordered plan shrinks until the two are not clearly distinguishable in the runs collected. The ordered plan takes about the same time at every pool.

The sort may contribute in the zone of transition. By the median, the original reaches a pool free of final-aggregate spills at `2g` with and without the `ORDER BY`. At 1 GB, however, its final aggregate does not spill in 5 of 10 completed runs of the query without a sort, and in 0 of 10 of the query with one. That is compatible with a contribution of the sort, for instance by competing for the pool.

With more rows the original goes further. A prefix is a distinct pair `(col_1, col_2)`, and the keys under it are the distinct grouping keys that share it. The series on rows has the base layout at 6 and 24 million rows in two ways, with more prefixes or with more keys under each prefix, and runs each size under 256 MB and under a pool large enough for the final aggregate of the original not to spill: 4 GB for the base case, 32 GB at 6 million rows, 128 GB at 24 million. These are limits of the pool, not memory the process holds.

| Dataset | Ordered against original, 256 MB | Ordered against original, large pool |
|---|---|---|
| 600,000 rows | 40 percent less | 0 percent more |
| 24 million rows, more keys under each prefix | 45 percent less | 16 percent more |
| 24 million rows, more prefixes | 49 percent less | 9 percent more |

Under 256 MB the ordered plan keeps its advantage at every size. At 24 million rows the original also fails some of its runs there, completing 4 of 5 and 3 of 5, and its times are those of the runs that complete. Under the large pool the original is the faster plan at every size above the base case. The benefit of keeping the order therefore belongs to a regime of memory: it is large where the unordered aggregation spills, and it is not guaranteed where that aggregation has room.

## 4. The original's aggregate is refused at its quota while the pool has capacity

The fair pool divides its limit among the operators that can spill. A consumer is the registration of an operator with the pool, and every consumer that can spill has a quota: the limit, less what the consumers that cannot spill hold, divided by the number of those that can and are registered at that moment. A request that would take a consumer above its quota is refused. The [description of the pool](https://github.com/apache/datafusion/blob/e1aa7d956a5aa67452c9e8bd2a033599767055d8/datafusion/execution/src/memory_pool/pool.rs) gives the formula and warns that it will sometimes cause spills even when there was sufficient memory to avoid them.

The lab shows where this documented rule applies in the base case, with what numbers, and who holds the limit when a request is refused. At 256 MB the pool refuses requests of the original plan 21 times in median. Of the refusals the traces record in detail, 106 of 106 are above the quota computed for that moment, and in 106 of 106 the request fitted the pool. The pool itself peaks at 181 MB of 256. An operator is refused at its own quota while the pool as a whole still has capacity.

These observations come from traced copies of the two builds, which wrap the memory pool and record what each consumer reserves, what it is refused and what was held at that moment. They keep the first and the last events of a run in detail, with complete counters. The lock the wrapper takes can change how tasks interleave, so every traced configuration also runs with the plain binary: the two complete the same number of runs in 39 of 39 configurations, and their spills are not always equal. What the traces attribute describes the traced runs, and no time of a traced run is used.

The two plans reserve very different amounts for their final aggregate. That of the original peaks at 62.9 MB at 256 MB, 127.3 MB at 512 MB and 361.2 MB at 2 GB, where it is no longer refused while the sort still is. That of the ordered plan peaks at 21.6 MB at 256 MB, and the ordered plan records 0 refusals. A peak here is the largest sum of what the consumers of a class, such as all the streams of the final aggregate, held at one moment, and it is a reservation. The reservations seen under a limit are not the need of the query: that of the original's aggregate grows with the pool until it is no longer refused, and the other operators can change with it.

The series on rows shows how differently the two aggregations grow. The first column of peaks is the ordered plan under 256 MB, where its aggregation is not refused at any of these sizes; the second is the original under the large pool, where its final aggregate is not refused either. Prefixes and keys per prefix are counted on the source table, not on what reaches each partition of the final aggregate.

| Dataset | Prefixes | Keys per prefix, mean (largest) | Ordered final aggregate, peak MB | Original final aggregate, peak MB |
|---|---:|---|---:|---:|
| 600,000 rows | 114,345 | 5.25 (largest 21) | 21.6 | 361.2 |
| 24 million, more keys | 115,500 | 207.47 (largest 395) | 19.1 | 12,338.9 |
| 24 million, more prefixes | 4,573,880 | 5.25 (largest 24) | 19.5 | 12,340.0 |
| 6 million, concentrated | 231 | 21633.38 (largest 31151) | 30.7 | 2,885.6 |

The reservation of the original's final aggregate grows with the rows. That of the ordered one stays level when the prefixes grow. With the prefixes fixed and up to forty times the keys under each it stays level too: in this range, more keys under a prefix do not raise it. Only the dataset with its keys concentrated under few prefixes reserves more, and it does not isolate the keys per prefix: a sixth of its rows are duplicates and it has fewer distinct keys than the other datasets of 6 million rows.

## 5. Many ordered groups cost time, memory and descriptors

Carrying order through the plan is not free, and the engine says so: the [repartition's own documentation](https://github.com/apache/datafusion/blob/e1aa7d956a5aa67452c9e8bd2a033599767055d8/datafusion/physical-plan/src/repartition/mod.rs) calls preserving order more expensive at runtime, to be asked for only when an operator after it can use it. A repartition that preserves order merges its inputs in each of its outputs, and those merges reserve memory that cannot spill. The lab measures the costs as the ordered groups grow in number.

### With about 1,200 groups the ordered plan loses under a small pool

With 1,200 files generated for total overlap, the scan of the ordered plan has 1,196 groups.

| Pool | Original seconds | Ordered seconds | Ordered final-aggregate spills, median |
|---|---:|---:|---:|
| 128 MB | 0.703 | 0.792 | 26 |
| 256 MB | 0.722 | 0.587 | 0 |
| 512 MB | 0.694 | 0.585 | 0 |

The ordered plan loses at the smallest budget and wins at the larger ones. Its repartition spills at every budget; what changes at 128 MB is the final aggregate, which spills there. At that budget the final aggregate of the original spills in every run too, at least 22 times, and the original is still the faster plan: spills in the unordered aggregation are not enough for the ordered plan to win.

With few groups the gain remains at the same number of files. At 256 MB, 1,200 files in the base layout form 5 groups, and the ordered plan takes 0.417 seconds against 0.728. The two layouts also differ in how rows are assigned to files.

A separate series at 128 MB varies the streams. Its times are those of its own runs with the plain binary, and its peaks are medians of the traced runs.

| Ordered streams | Original seconds | Ordered seconds | Ordered against original | Ordered final aggregate, peak MB |
|---:|---:|---:|---|---:|
| 150 | 0.623 | 0.625 | within the quartiles | 94.8 |
| 300 | 0.647 | 0.631 | faster | 82.9 |
| 599 | 0.656 | 0.689 | within the quartiles | 85.9 |
| 1,196 | 0.710 | 0.783 | slower | 85.9 |

The large advantage of the base case does not appear at any of these sizes. At 150 streams, where the final aggregate of the ordered plan already spills, the two plans are within the quartiles; at 300 the ordered plan is faster by a small margin; at 599 they overlap again, and at 1,196 the ordered plan is slower.

The final aggregate is refused here under a condition different from that of the base case. With about 1,200 streams, at its refusals 2 to 5 consumers that can spill are registered, and the consumers that cannot hold 2 to 105 MB of the 128. Where the merges of the repartition keep most of the limit, little is left to divide among the few consumers that can. In the base case, at the refusals of the original's final aggregate, the consumers that cannot spill hold nothing.

A larger reservation of the aggregate itself is not needed for those refusals. From 150 to about 1,200 streams its median peak does not rise. Its peak by run with about 1,200 streams is 24.7, 82.9, 85.9, 85.9, 85.9 MB: in one run it stays near the base case, and the final aggregate is refused in that run too.

### The workaround stops completing as the groups grow

Raising `target_partitions` to the number of groups makes the original binary accept them. In the base case the workaround takes 0.267 seconds and 452 MB RSS, against 0.351 and 354 for the ordered plan with two downstream partitions. Across the deduplication cases of the matrix, which go up to twelve groups, it is faster than the ordered plan in most of the cases where both run, and has a larger peak RSS in all of them. It raises the parallelism downstream together with accepting the order.

With more groups it stops completing: with 120 groups it completes 0 of 10 runs at 256 MB and 2 of 10 at 2 GB (3 of 10 in a second set of runs of the same configuration), and with about 1,200 groups 0 of 10 at 2 GB.

That more partitions leave less memory to each under the fair pool is in DataFusion's [configuration guide](https://github.com/apache/datafusion/blob/e1aa7d956a5aa67452c9e8bd2a033599767055d8/docs/source/user-guide/configs.md), which warns that a higher `target_partitions` can make the spilling path more frequent. The traces say what holds, with 120 groups, at the request that ends the query. Under the fair pool the error names the ordered final aggregate: it asks 3.3 MB holding nothing, against a quota of 0.3 MB at 256 MB and of 2.4 MB at 2 GB. Most of what the pool has reserved at that moment is held by the merges of the repartition's outputs, which cannot spill and so narrow every quota. Under the greedy pool, which has no quotas, the error names those merges, and the pool is nearly full: 255.8 MB of 256, most of it held by the repartition. At the refusals the two pools show different conditions: quotas too small in one, overall capacity nearly exhausted in the other.

Passing to the greedy pool is not enough for the workaround to complete: with 120 groups it completes 0 of 10 runs at 256 MB and 0 of 10 at 2 GB. The traces support that the quotas of the fair pool contribute to the refusals under that pool; removing them does not remove the failure.

With statistics grouping disabled and the same target, the 1,200 files are spread over the partitions without regard to their bounds and the plan is no longer ordered: it completes 10 of 10 runs at 2 GB and 0 of 10 at 256 MB. This bounds the failure without isolating its cause: at 2 GB the contrast points at the ordered path, with several operators changing at once; at 256 MB the number of partitions is enough for the query to fail.

### Resident memory grows with streams and outputs, and most of the excess depends on page policy

With about 1,200 streams the ordered plan has a much larger RSS than the original. For Q1, which reads all columns and merges ordered streams, the excess grows by 0.34 to 0.38 MB per stream across three series of runs. For the deduplication it grows with the outputs of the repartition as well: going from two to eight outputs adds 1.4 to 1.6 GB in the same series. These coefficients describe RSS in a specific allocator and page environment; the report gives them per series, with an experiment that crosses streams with outputs.

Most of the excess disappears when the transparent huge pages of the Linux kernel are disabled for the measured process. A separate experiment, with its own runs, repeats the deduplication with 1,196 ordered streams under a 256 MB pool, with the page policy of the machine and without huge pages.

| Q3, 256 MB pool | As configured | Huge pages disabled |
|---|---:|---:|
| Ordered peak RSS, MB | 2081 | 596 |
| Original peak RSS, MB | 635 | 488 |
| Difference between peaks, MB | 1446 | 108 |
| Added ordered RSS from two to eight outputs, MB | 1343 | 283 |
| Ordered elapsed seconds | 0.582 | 0.603 |

Elapsed time changes relatively little. Partly used huge pages are a plausible mechanism, consistent with the kernel's documented behavior; the experiment does not map individual buffers to pages. The remaining 108 MB is a difference between two peaks, and both can include retained allocations, stacks and other residency. The resident pages consume physical memory and can matter to external limits; what the experiment separates is residency from live application data.

### The ordered plan needs more open files

Under a process limit on open files, the ordered plan with about 1,200 streams fails at limits through 4,096 and completes at 8,192, while the original completes already at 1,024. These are the limits tested, not an exact minimum. A sampler outside the process, reading `/proc`, counts 4,505 descriptors for the ordered plan, dominated by 4,495 temporary spill files, against 32 for the original. The number of Parquet files is therefore not a sufficient predictor of the pressure on descriptors.

### Order can limit how much of the machine a plan uses

With twelve files in two ordered groups, both builds produce the same plan and the guard plays no part. When the files do not overlap and the two groups alternate over the key range, the plan takes 0.483 seconds, against 0.404 when the files overlap two at a time. Without overlap, the ranges the two producers are reading at any moment are disjoint. The merge consumes one producer for a while before using the other, and the second can run ahead only as far as buffering permits: in the repartition each input is blocked once all its channels to the outputs hold pending data. A separate experiment measures the average cores the process uses, CPU time over wall time, at several batch sizes. Larger batches close the gap between the two layouts at 32,768 rows, which supports backpressure as an explanation in combination with that channel design. The largest batch size has a price: at 131,072 rows, 4 of 20 runs fail, and both layouts take longer among completions.

## 6. String views change the outcome, and the accounting alone does not explain it

By the engine's default the strings of the lab are read as string views. Short views fit their bytes inline; long ones refer to external buffers. Reading the same values as ordinary `Utf8` changes outcomes. With wide strings, a variant of the dataset in which the string columns `col_1`, `col_3` and `col_4` are all longer than the twelve bytes a view holds inline, the ordered gain at 128 MB is 10 percent with views, where the original completes 8 of its 10 runs, and 41 percent with `Utf8`. With about 1,200 streams at 128 MB and `Utf8`, the final aggregate of the ordered plan spills 0 times and the plan is faster than the original (0.586 against 0.763 seconds). For the workaround with 120 groups it changes little: 0 of 10 runs complete at 256 MB and 2 of 10 at 2 GB.

The source suggests one way in which views could raise the pressure. The hash repartition performs one `take` for an input batch and slices the reordered batch for its outputs, charging each slice with the capacity of the buffers it refers to. Views can retain source string buffers shared by all fragments, which makes the accounted amount much larger than the rows in one fragment suggest. A third build, `accept-groups-accounting`, tests this: it keeps views and charges the repartition with the bytes associated with each slice's rows.

| Ordered plan, wide strings, 128 MB | Elapsed seconds | Final spills, median | Repartition spills, median |
|---|---:|---:|---:|
| Views, engine accounting | 0.627 | 14 | 8 |
| Views, slice accounting | 0.645 | 14 | 4 |
| `Utf8` | 0.405 | 0 | 0 |

The slice accounting changes the spills of the repartition. The final aggregate still spills the same median number of times, and elapsed time is slightly worse, 0.645 seconds against 0.627, beyond the quartiles. For this configuration, reducing the reservation of the slices does not resolve the spills of the final aggregate. String views are involved in the pressure; whether representation, copying, retained buffers or accounting produces it is not identified.

## 7. What the lab establishes and where it stops

None of the mechanisms named here is new to the engine. What the lab adds is a diagnosis of one case: where the ordering is discarded, what accepting it changes and where that stops paying, who holds the limit at the request that ends a query, and a way to repeat the measurements when the engine changes.

The guard is a demonstrated cause of the plan change in the base case. In the base layout and in the series on rows, keeping the order improves execution substantially where the unordered aggregation spills. Carrying many ordered streams has costs in time, reservation, residency and descriptors, and with about 1,200 streams under a small pool they reverse that advantage. That is evidence against removing the guard unconditionally. The experiments expose the trade-offs of the choice the guard makes; they supply no portable threshold or cost formula for a policy that would replace it.

Two questions can be taken upstream apart from that choice: whether `EXPLAIN` could show why the ordering was discarded, and whether the stricter treatment of files that touch at a boundary, compared with `MinMaxStatistics::is_sorted`, is intended.

This is a synthetic study on one machine and one engine commit. Outside the experiment on rows, query durations are about a second or less and fixed costs per stream are prominent. The greedy pool appears only where an experiment changes the pool, and no data is of production scale. The quota in the traces is computed by the wrapper after the pool has decided. Reservations and resident memory are measured; which operator owns the live allocations is not.

## Reproduction

The executable workflow and prerequisites are in the repository's README and `scripts/run_lab.sh`. The script rebuilds datasets, checks plans and results, runs the experiments and generates the numerical report using recorded binary provenance.

Every number in this narrative is in `results/figures.tsv`, in the result tables or in `RESULTS.md`, as recorded at lab commit `dcf1f1e7`; none is measured apart from them.
