# Ordered file groups: when preserving order helps

Sorted Parquet files do not by themselves produce a sorted scan. DataFusion can concatenate files into one stream only when their statistics show that the value range of one file ends before the range of the next. Files whose ranges overlap must remain in separate streams; a later operator can merge those streams, but the scan no longer has one continuous order.

This distinction is the starting point of the lab. The files are written in the declared order, so the experiment controls both the physical layout and the statistics that DataFusion sees. The question is what happens when the engine preserves that order through the rest of the plan.

## The decision that changes the plan

For a listing table, DataFusion can build file groups from the files' minimum and maximum values; the setting `split_file_groups_by_statistics` enables this statistics grouping. It starts with as many empty groups as `target_partitions`, the configured number of partitions, and considers the files by increasing minimum. A file can join an empty group or a group whose last maximum is strictly below the file's minimum; among those groups it chooses the one with the fewest files. If no group qualifies, it creates another group.

The statistics establish that files can be concatenated. They do not establish that the rows inside an individual file are sorted. The generator used here writes the rows in the declared `(col_1, col_2)` order, so the experiment satisfies that additional condition.

At the DataFusion commit used by the lab, `e1aa7d956`, the proposed groups are accepted only when their number does not exceed `target_partitions`. When this check rejects the groups, the scan keeps its initial grouping. A later optimizer step may then redistribute files by size and split them into byte ranges; the resulting scan may no longer advertise the ordering that the statistics had established. DataFusion logs the reason for rejecting the groups at debug level, but the recorded plans do not expose it.

The comparison is strict at the boundary. If one file ends exactly where the next begins, the grouping code puts them in different groups because it requires `max < next_min`. `MinMaxStatistics::is_sorted` in the same engine accepts `max <= next_min`. In a 1,200-file layout, 248 touching pairs therefore produce 5 groups although 4 would be sufficient.

The lab asks whether the work saved by preserving order justifies the cost of carrying that order through the plan. Runtime and memory are separate outcomes: a faster plan can require more resident memory or more open files.

## The experiment

The base dataset contains 600,000 rows in twelve Parquet files. The files are sorted by `(col_1, col_2)`, and each file overlaps the next three. Their bounds require four ordered groups, while the configured `target_partitions` is two. The check therefore determines whether the ordered grouping reaches the scan.

The workload is a deduplication query. It groups by six columns, beginning with the two-column sort key, and then orders the result by that same key. The input order is therefore useful to the aggregate: once the first two grouping columns change, the previous prefix cannot appear again in the stream. The aggregate can finish state earlier instead of retaining all prefixes until the input ends.

The lab compares four configurations:

| Configuration | Change | Purpose |
| --- | --- | --- |
| `original` | Unmodified build, statistics grouping enabled, target two | Show the rejected grouping and its plan |
| `accept-groups` | Remove the check, keep target two | Measure the whole effect of accepting the extra ordered groups |
| `original-target` | Unmodified build, target raised to the required count | Test the configuration workaround |
| `original-split-off` | Unmodified build, statistics grouping disabled | Provide a reference without statistics grouping |

The main comparison is between `original` and `accept-groups`. Removing the check is a small source change, but it changes several downstream decisions at once: the scan streams, the repartition, the aggregate modes and the presence of a sort. The measurements therefore describe the combined execution effect, not an isolated cost of one conditional.

The machine is a Ryzen 9 7950X3D with eight pinned physical cores. Each configuration of the main matrix runs with a warmup and ten measured executions. The matrix uses the fair memory pool at 128, 256 and 512 MB; other experiments use larger pools. The matrix records 620 executions, and 620 complete; a separate check of the deterministic results passes in 62 of 62 cells. The report calls one plan faster when its third quartile lies below the first quartile of the other. The full protocol and the complete measurements are in [`DESIGN.md`](DESIGN.md) and [`RESULTS.md`](RESULTS.md).

## What happens in the base case

At a 256 MB pool, the ordered plan completes the deduplication in 0.351 seconds, compared with 0.603 seconds for the original plan. The statistics-grouping-disabled reference takes 0.603 seconds, within the quartiles of the original, so the difference comes from preserving the ordered groups rather than from the grouping setting by itself.

The original plan sorts, and it spills in the sort and in the final aggregate. The ordered plan does neither. The operator metrics accumulate time over the partitions, so they can exceed the elapsed time of the query. In [those metrics](RESULTS.md#time-by-operator-as-the-engine-reports-it) the final aggregate of the ordered plan computes for 0.378 seconds, compared with 0.888 seconds for the original plan. The ordered repartition spends longer delivering data, 0.815 seconds against 0.233; that time includes waiting. A gain remains in the query without an explicit `ORDER BY`, in which neither plan sorts: the ordered plan completes in 0.306 seconds versus 0.567 seconds.

[Three other queries](RESULTS.md#a1-the-consumer-of-the-order) bound the effect. A query with only the `ORDER BY` also gains, with a merge in place of a sort: 0.027 seconds against 0.049. A query that groups on the complete sort key shows no gain, 0.052 seconds against 0.050. A query that groups on columns outside the sort key shows none either: the optimizer discards the ordering it cannot use. The order helps only where a downstream operator can consume it, and consuming it is not always enough.

The advantage depends on memory. In the [series on larger pools](RESULTS.md#the-base-case-with-larger-pools), with 512 MB and 1 GB the ordered plan remains near 0.35 seconds (0.350 and 0.356) while the original plan is still affected by spills. At 2 GB, the original final aggregate no longer spills, and from there the two plans are not clearly distinguishable in the runs collected: 0.362 seconds against 0.357 at 2 GB, 0.346 against 0.357 at 4 GB. Preserving the order removes a source of memory pressure; where that pressure is absent, it brings no advantage here.

The [larger-row experiments](RESULTS.md#more-rows-for-the-same-files) show the same boundary. With 24 million rows, the ordered plan reserves much less memory for the final aggregate and remains faster under a 256 MB pool. Under a pool large enough for its final aggregate not to spill, the original plan is the faster one: in the two datasets of 24 million rows the ordered plan takes 16 percent more and 9 percent more time. The result is therefore conditional: preserving order helps when it prevents expensive state from spilling, not as a universal rule.

## Why the memory numbers matter

DataFusion's fair memory pool divides the available quota among registered spillable consumers after accounting for memory held by consumers that cannot spill. A request can be refused even while the pool still has unused capacity, because the requesting consumer has reached its fair share. This is documented behaviour: the [description of the pool](https://github.com/apache/datafusion/blob/e1aa7d956a5aa67452c9e8bd2a033599767055d8/datafusion/execution/src/memory_pool/pool.rs) warns that it will sometimes cause spills even when there was sufficient memory to avoid them.

That is what happens in the base case. At 256 MB, the original final aggregate reaches its fair quota and receives a refusal while the pool as a whole is still below its limit. The pool peaks at about 181 MB of 256 MB. The ordered final aggregate reserves less memory, and the ordered plan records no refusal. The reservation is a property of the execution plan and the pool's sharing policy; it is not a direct measurement of the query's intrinsic memory requirement.

The row series makes the difference clearer. The reservation of the ordered final aggregate stays near 20 MB as the data grows from 600,000 to 24 million rows: 21.6 MB in the base case, 19.1 and 19.5 MB in the two larger datasets. That of the original aggregate grows from about 361.2 MB to more than 12 GB (12,338.9 and 12,340.0 MB), measured under a pool large enough for it not to be refused. Releasing completed prefixes is the natural reading of the ordered figures, but the lab does not identify what the ordered aggregate holds. Its reservation stays level also in the dataset with forty times the keys under each prefix, where that reading alone would predict growth.

These figures come from [traced runs](RESULTS.md#what-the-memory-pool-grants-and-refuses). The tracing wrapper records reservations, refusals and held memory, and the lock used by the trace can change task interleaving. Every traced configuration also runs with the plain binary: the two complete the same number of runs in 39 of 39 configurations, but their spill counts are not always equal. The trace supports the diagnosis of the refusal; it is not used to compare execution time.

## When preserving order becomes expensive

The base case has only four ordered groups. The cost changes when the number of streams grows, and DataFusion's [repartition documentation](https://github.com/apache/datafusion/blob/e1aa7d956a5aa67452c9e8bd2a033599767055d8/datafusion/physical-plan/src/repartition/mod.rs) itself calls preserving order more expensive at runtime. In the total-overlap layout, 1,200 files produce 1,196 ordered groups. The order-preserving repartition must merge those inputs in each of its outputs, and the merge reservations cannot spill. The plan also keeps many files open.

At 128 MB, the ordered plan is slower than the original plan (0.792 seconds against 0.703) and the final aggregates of both plans spill. At 256 and 512 MB, the ordered plan is faster in this particular layout (0.587 against 0.722 seconds at 256 MB), but its repartition still spills and the process holds more resident memory. In a [separate series at 128 MB](RESULTS.md#why-plans-with-many-ordered-groups-fail-or-lose), from 150 to 1,196 streams, the ordered plan is faster at one size, within the quartiles at two and slower at the largest. The difference does not change monotonically, and the large advantage of the base case appears at none of these sizes.

The [workaround of raising `target_partitions`](RESULTS.md#raising-target_partitions-when-the-groups-are-many) shows a second cost. It can produce an ordered plan without changing the source, but it also increases downstream parallelism. In the base case it is faster than the ordered plan while using more resident memory: 0.267 seconds and 452 MB against 0.351 and 354. With many groups it stops completing. With 120 groups it completes 0 of 10 runs at 256 MB and 2 of 10 at 2 GB; with about 1,200 groups, 0 of 10 at 2 GB.

The fair pool is not the whole reason: under the greedy pool, which has no quotas, the workaround with 120 groups completes 0 of 10 runs at 256 MB and 0 of 10 at 2 GB. DataFusion's [configuration guide](https://github.com/apache/datafusion/blob/e1aa7d956a5aa67452c9e8bd2a033599767055d8/docs/source/user-guide/configs.md) warns that a higher `target_partitions` can make the spilling path more frequent. In the traces, the merge consumers hold memory that cannot be redistributed to the aggregate. Increasing the target therefore exchanges one cost for another; it is not a general way around the check.

The large-stream layout also exposes operating-system costs. The ordered plan keeps many more files open, most of them temporary spill files. It fails at [open-file limits](RESULTS.md#the-open-file-limit) up to 4,096 and completes at 8,192, while the original completes at 1,024. A sampler outside the process counts up to 4,505 descriptors for the ordered plan, 4,495 of them temporary spill files; the original plan holds 32. Resident memory shows a related effect: the excess RSS of the ordered plan grows with the number of streams and with the outputs of the repartition. Most of that excess depends on the page policy. With [transparent huge pages disabled](RESULTS.md#how-much-of-the-resident-memory-is-transparent-huge-pages) for the measured process, the difference between the peaks of the two plans falls from 1446 to 108 MB. The experiment does not identify the complete allocation path.

## Other checks

The [wide-string experiment](RESULTS.md#string-views-and-the-repartitions-accounting) changes the width and the representation of the string columns. Short strings use inline views; long strings retain references to external buffers. With wide strings and the default string-view representation, the ordered plan is about 10 percent faster than the original at 128 MB, where the original completes 8 of its 10 runs. Reading the same values as ordinary UTF-8 strings increases the gain to about 41 percent: the ordered plan no longer spills, while the original still does.

A third build charges the repartition only for the bytes of each slice's rows, where the engine charges the full capacity of the buffers a slice shares. That reduces repartition spills and leaves those of the final aggregate unchanged. The experiment shows that representation and accounting affect the result, but it does not establish a single cause for the whole difference.

With twelve files in two ordered groups, both builds produce the same plan and the check is irrelevant. The layout in which the files do not overlap is slower than the one in which each file overlaps the next: 0.483 seconds against 0.404 at the default batch size. A plausible explanation is [backpressure](RESULTS.md#depth-1-against-depth-2-backpressure). When the ranges being read are disjoint, the ordered merge consumes one producer for a while, and the other is blocked once its channels are full. Larger batches narrow the gap; very large batches make both layouts slower and sometimes fail. This is another reason not to treat the number of ordered groups as a complete cost model.

## What the lab establishes

The base case shows one chain of events. The check rejects an ordered grouping because it requires more groups than `target_partitions`. Accepting those groups preserves an input order that lets the deduplication aggregate finish prefixes early. The ordered plan then reserves far less for that aggregate, does not spill, and is faster at a constrained memory pool. The check is the demonstrated cause of the change of plan. The gain is the effect of the whole change, which the lab does not divide among its parts.

None of these mechanisms is new to DataFusion. The use of order by aggregations, the quotas of the fair pool, and the cost of preserving order and of more partitions are documented. What the lab adds is a diagnosis of one case and a way to repeat the measurements.

The wider matrix gives the boundary of the result. The benefit shrinks when the original plan has enough memory, and it can reverse when preserving order requires too many streams. Ordered streams consume merge memory, resident memory and file descriptors. Raising `target_partitions` can reproduce the order but also increases parallelism, and with many groups the query fails, under the fair pool and under the greedy one.

The lab therefore does not support unconditional removal of the check. It raises two questions for the engine: whether `EXPLAIN` could show why the ordering was discarded, and whether the strict comparison at file boundaries, which differs from `MinMaxStatistics::is_sorted`, is intended. The right decision on the groups depends on the overlap pattern, the downstream operator, the memory budget and the number of streams; the lab identifies these dimensions and supplies no threshold.

The experiment is synthetic and runs on one machine and one DataFusion commit. Most timings are sub-second, and the lab measures reservations and resident memory rather than complete live ownership of every allocation. The code, exact commands, all measurements and their provenance are in the repository; the results are those of lab commit `dcf1f1e7`.
