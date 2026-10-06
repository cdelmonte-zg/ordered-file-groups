# Reading the ordered-file-groups experiments

*Why each experiment exists, what it establishes, and where the explanation stops.*

## 1. The question and the intervention

Sorted Parquet files do not automatically produce a sorted scan. Concatenating two files preserves an ascending order only when their boundary values permit it. Overlapping files may require separate streams, which can then be merged.

A file scan in DataFusion has one output partition for each file group, and reads the files of a group one after the next. In the path studied here, DataFusion attempts to build ordered groups from statistics, starting with `target_partitions` empty groups. Files are considered by increasing minimum; a file is appended to the least populated group that is empty or whose last file's maximum is strictly below the file's minimum. When no group qualifies, another one is added. These statistics establish whether files can be concatenated; they do not prove that the rows within each file are sorted. The lab declares an order which its generator actually writes.

At DataFusion commit `e1aa7d956`, the listing-table code accepts the proposed groups only when their count does not exceed the target. If it rejects them, it retains the initial grouping; a later optimizer step can redistribute files by size and split them into byte ranges. The resulting scan may no longer advertise an order.

The base dataset has 600,000 rows in twelve files sorted by `(col_1, col_2)`. Its bounds require four ordered groups while the configured target is two. This makes the guard decisive. The two primary builds differ only in whether that guard remains:

| Variant | Intervention | Purpose |
|---|---|---|
| `original` | Unmodified commit, statistics grouping enabled, target two | Observe the rejected grouping and subsequent plan |
| `accept-groups` | Remove the guard, keep target two | Measure the whole effect of accepting extra ordered groups |
| `original-target` | Original build, target raised to the required count | Compare the existing configuration workaround |
| `original-split-off` | Original build, statistics grouping disabled | Base-case reference for the grouping setting |

The primary intervention is small in source code, but broad in execution. It changes scan streams, aggregate modes, repartition behavior and the presence of a sort. Its effect is the combined consequence of those changes, and the patch exists to measure it.

The practical question is whether useful downstream work saved by order outweighs the resources needed to carry that order through the plan. Time and memory are separate outcomes: a faster plan can be more expensive to keep resident.

## 2. What is controlled and what is checked

The machine is a Ryzen 9 7950X3D. Governor and energy preference are set to `performance`, boost remains enabled, swap is disabled, and the measured processes are pinned to the eight physical cores sharing the larger cache, including their SMT siblings. This controls placement and power policy; it does not lock the processor to one frequency.

The matrix uses the fair memory pool at 128, 256 and 512 MB. Each configuration has a warm-up and ten recorded runs, with variant order rotated. The many-stream experiment uses five ordered runs and three baseline runs per cell in each of three series. The depth and string experiments use ten runs, as do those on larger pools, on the raised target and on causes; the experiment on rows and the traced runs use five; the open-file tests and external process observations use three. Repetition across series explores variation on this one machine.

The matrix records 620 executions, 620 of which complete. A run that fails is retained and excluded from timing summaries. The separate correctness check passes 62 of 62 cells. It compares multisets of deterministic columns, checks the requested sort key, and for deduplication checks the number of distinct keys against the manifest. Unordered `first_value` results may differ legitimately and are excluded. Rows tied on the requested sort key need not appear in an identical sequence.

The comparison queries are executed through a `COPY` sink so that collecting terminal output does not consume the query's pool. The plan beneath that sink is checked against the timed plan's relevant properties. Correctness is therefore checked on a separate execution of a comparable plan.

The lab records three kinds of time:

- The matrix's CLI `Elapsed` includes planning and query execution for the measured statement, including listing and statistics work involved in constructing the scan.
- Whole-process wall time and user/system CPU time come from `/usr/bin/time`. The depth experiment uses their ratio as average cores used.
- Operator metrics are accumulated durations. `elapsed_compute` times instrumented computation; `send_time` includes delivery and waiting.

RSS is the resident memory of the process, as the operating system measures it. A reservation is what an operator has declared to the memory pool; the pool's limit applies to reservations. `output_bytes` adds up the size of the batches an operator emitted, as Arrow accounts for their buffers; a buffer shared by several batches may be counted more than once. The engine prints large counts with a suffix and limited precision: `2.12 K` stands for approximately 2,120.

The report calls one variant faster when its third quartile lies below the other's first. This is a declared descriptive rule, not a hypothesis test or a guarantee of statistical significance. For configurations with failures the report gives the completed runs beside the time of those that complete.

`DESIGN.md` states predictions for the axes of the matrix, written before the run, and `RESULTS.md` checks each one against a declared rule in its table of predictions; not all of them hold. The experiments on larger pools, on the raised target, on rows and on causes had a pilot. Their explanations and predictions were written before it; the hypothesis on the quotas of the pool, the readings of the traces and the two series on rows were written after it, and `DESIGN.md` marks them as such. The pilot had already shown the quotas: for that hypothesis the recorded runs repeat an observation under a fixed design, and are not a test of a prediction made blind.

The lab builds five binaries: the two primary builds, a third with a changed accounting used by one experiment, and a traced copy of each primary build. The traced ones wrap the memory pool and record what each consumer reserves, what it is refused and what held at that moment, forwarding every call unchanged. The lock they take can change how tasks interleave, so every traced configuration also runs with the plain binary: the two complete the same number of runs in 39 of 39 configurations. Their spills are not identical: the median spills of an operator are equal in 119 of 136 cells of that comparison, and elsewhere differ by a few units or a few percent. That makes the two comparable on what was recorded; it does not show that the wrapper perturbs nothing, and what the traces attribute describes the traced runs. No time of a traced run is used.

Three readings of the traces are fixed. The peak of a class of consumers is the largest sum of what they held at one moment: a reservation, not the sum of separate peaks and not resident memory. A refusal is read against the limit as reserved plus request. A release after a refusal is what the wrapper sees; it is consistent with a spill and is set beside the spills the operator reports, not observed as one.

## 3. The benefit and its boundary

Q3 groups by six columns, including the two-column sort prefix, and orders its output by that prefix. In the ordered plan, a change of prefix proves that the previous prefix will not return in that stream. The aggregate can finish those groups early. Partial aggregation still emits contributions which must be combined after redistribution; local completion is not completion across the entire query.

At 256 MB, Q3 changes from 0.603 seconds to 0.351, a reduction of 42 percent. The original spills in its sort and final aggregate; the ordered base case does not spill. This establishes a useful effect for the dataset and configuration. It does not yet explain how much comes from removing the sort, completing prefixes early, or avoiding final-aggregate spills.

The original build with statistics grouping disabled (`original-split-off`) takes 0.603 seconds, within the quartiles of the original. The setting alone does not move the original plan beyond the quartiles.

The operator measurements make the next question more precise:

| Metric, accumulated seconds | Original | Ordered |
|---|---:|---:|
| Sort `elapsed_compute` | 0.030 | absent |
| Final aggregate `elapsed_compute` | 0.888 | 0.378 |
| Partial aggregate `elapsed_compute` | 0.275 | 0.316 |
| Repartition `send_time` | 0.233 | 0.815 |

The largest reduction in recorded compute duration is at the final aggregate. The repartition spends longer delivering batches in the ordered plan. That increase can include backpressure and does not isolate the comparisons performed by the merge.

### Removing the sort from the question

Q5 is the same deduplication without the final `ORDER BY`. Neither plan sorts, but the ordered plan still takes 0.306 seconds against 0.567. The gain persists without a sort to remove. Together with the operator metrics, this supports an important role for aggregation, while leaving changes in the scan and repartition coupled to it. It does not yield additive shares of the Q3 speedup.

### Queries which use order differently

| Query | Original seconds | Ordered seconds | What the comparison establishes |
|---|---:|---:|---|
| Q1, only `ORDER BY` | 0.049 | 0.027 | An ordered scan and merge can beat the scan-and-sort plan |
| Q2, group on the complete sort key | 0.050 | 0.052 | `Sorted` mode does not ensure a time benefit when the hash alternative fits in memory |
| Q4, group without the sort key | 0.014 | 0.014 | The optimizer discards the unused ordering; the measured times overlap |

Q1 is the most direct sort-versus-merge case, but its scan organization changes too. Q2 prevents the argument from becoming "ordered aggregation is always faster." Q4 checks whether the scan intervention creates a benefit when the query has no use for the property. It is the negative control for this plan and workload.

### Where the benefit ends

Up to 512 MB, the largest pool of the matrix, the final aggregate of the original plan spills at every pool and that of the ordered plan at none. A separate experiment runs the base case under larger pools.

| Pool | Original seconds | Ordered seconds | Original final-aggregate spills, median |
|---|---:|---:|---:|
| 512 MB | 0.633 | 0.350 | 6 |
| 1 GB | 0.611 | 0.356 | 4 |
| 2 GB | 0.362 | 0.357 | 0 |
| 4 GB | 0.346 | 0.357 | 0 |

From the pool `2g` up, the final aggregate of the original no longer spills in any run, and the advantage of the ordered plan shrinks until the two are not clearly distinguishable in the runs collected. The ordered plan takes about the same time at every pool.

With more rows the original goes further. The base layout was generated at 6 and 24 million rows, and each size ran under 256 MB and under a pool large enough for the final aggregate of the original not to spill: 4 GB for the base case, 32 GB at 6 million rows, 128 GB at 24 million. These are limits of the pool, not memory the process holds.

| Dataset | Ordered against original, 256 MB | Ordered against original, large pool |
|---|---|---|
| 600,000 rows | 40 percent less | 0 percent more |
| 6 million rows, more keys under each prefix | 45 percent less | 11 percent more |
| 24 million rows, more keys under each prefix | 45 percent less | 16 percent more |
| 6 million rows, more prefixes | 45 percent less | 13 percent more |
| 24 million rows, more prefixes | 49 percent less | 9 percent more |
| 6 million rows, keys concentrated under few prefixes | 49 percent less | 8 percent more |

Under 256 MB the ordered plan keeps its advantage at every size; at 24 million rows the original also fails some of its runs there, completing 4 of 5 and 3 of 5, and its times are those of the runs that complete. Under the large pool the original is the faster plan at every size above the base case. The concentrated dataset differs from the others in more than its prefixes, as the next section says. The benefit of keeping the order therefore belongs to a regime of memory: it is large where the original aggregation spills, and it is not guaranteed where the hash aggregation has room.

### Memory budgets and duplicates inside the matrix

The budget series asks whether increasing available pool memory changes the gap. Across 128, 256 and 512 MB the base-case reductions are 45 / 42 / 44 percent. The ordered final aggregate does not spill at these budgets, while the original does. Inside the matrix the series therefore does not remove spill avoidance as an explanation: none of its budgets leaves both final aggregates free of spills. The larger pools above reach one.

| Pool | Original peak RSS, MB | Ordered peak RSS, MB | Original sort spills, median |
|---|---:|---:|---:|
| 128 MB | 448 | 390 | 15 |
| 256 MB | 496 | 354 | 8 |
| 512 MB | 528 | 358 | 4 |

The original's spill count falls without a corresponding transformation in elapsed time. That weakens a simple proportional relationship between spill count and query duration; it does not show that spills are unimportant. A spill count says neither how much data moved nor how its work overlaps with other work.

The duplicate experiment keeps the total row count and changes the number of distinct grouping keys. With roughly half the rows duplicated, elapsed time becomes 0.388 seconds for the original and 0.195 for the ordered plan, a 50 percent reduction. Both benefit from fewer distinct keys. The ordered plan benefits proportionally more, but this experiment does not isolate prefix completion as the reason: aggregate state, reductions in rows and spill volumes change together.

## 4. What the plans reserve

The fair pool gives every consumer that can spill a quota: the limit, less what the consumers that cannot spill hold, divided by the number of those that can and are registered at that moment. This is documented behaviour: the [description of the pool](https://github.com/apache/datafusion/blob/e1aa7d956a5aa67452c9e8bd2a033599767055d8/datafusion/execution/src/memory_pool/pool.rs) gives the formula and warns that it will sometimes cause spills even when there was sufficient memory to avoid them. The traced runs do not establish that rule. They show where it applies in this case, with what numbers, and who holds the limit when a request is refused.

In the base case at 256 MB the original plan is refused 21 times in median. Of the refusals kept, 106 of 106 are above the quota computed for that moment, and in 106 of 106 the request fitted the pool. The pool itself peaks at 181 MB of 256. An operator is refused at its own quota while the pool as a whole still has capacity.

The reservations grow with the pool, that of the sort most of all. For the final aggregate the peak is 62.9 MB at 256 MB, 127.3 MB at 512 MB and 361.2 MB at 2 GB, where it is no longer refused while the sort still is. The final aggregate of the ordered plan peaks at 21.6 MB at 256 MB, and the ordered plan records 0 refusals.

After a refusal the final aggregate and the sort release memory. In the base case the counts of those releases follow the spills these operators report, a little short of them; with many streams they do not, and the table of `RESULTS.md` sets the two side by side. The partial aggregate is refused and releases too, and reports no spill: a release is not always one.

This does not show that the original plan would complete without spills inside the same limit if its aggregate were allowed more. The reservations and the peaks of the other operators can change when it holds more, and the reservations seen here are not the need of the query.

The series on rows shows how differently the two aggregations grow. The first column of peaks is the ordered plan under 256 MB, the second the original under the large pool, where its final aggregate is not refused and its sort still is.

| Dataset | Prefixes | Keys per prefix, mean (largest) | Ordered final aggregate, peak MB | Original final aggregate, peak MB |
|---|---:|---|---:|---:|
| 600,000 rows | 114,345 | 5.25 (largest 21) | 21.6 | 361.2 |
| 6 million, more keys | 115,500 | 51.93 (largest 115) | 20.2 | 3,092.4 |
| 24 million, more keys | 115,500 | 207.47 (largest 395) | 19.1 | 12,338.9 |
| 6 million, more prefixes | 1,143,628 | 5.25 (largest 24) | 19.5 | 3,093.5 |
| 24 million, more prefixes | 4,573,880 | 5.25 (largest 24) | 19.5 | 12,340.0 |
| 6 million, concentrated | 231 | 21633.38 (largest 31151) | 30.7 | 2,885.6 |

The reservation of the original's final aggregate grows with the rows. That of the ordered one stays level when the prefixes grow, as predicted. With the prefixes fixed and ten to forty times the keys under each it stays level too, and that is against the prediction: in the range tried, more keys under a prefix did not raise it. Only the concentrated dataset reserves more. It does not isolate the keys per prefix: a sixth of its rows are duplicates and it has fewer distinct keys than the other datasets of 6 million rows. Prefixes and keys per prefix are counted on the source table, not on what reaches each partition of the final aggregate.

The ordered plan reserves far less for its aggregation, and with both plans under a 256 MB pool, in the base case and in the series on rows, its aggregation is not refused where that of the original is; its repartition is refused a few times at the larger sizes. That is what the traces connect; they do not suppose that the two plans have the same quotas, which change during a run and depend on the other consumers.

## 5. The cost of keeping the order

Carrying order through the plan is not free, and the engine says so: the [repartition's own documentation](https://github.com/apache/datafusion/blob/e1aa7d956a5aa67452c9e8bd2a033599767055d8/datafusion/physical-plan/src/repartition/mod.rs) calls preserving order more expensive at runtime, to be asked for only when an operator after it can use it. The costs appear as the ordered groups grow in number, and the experiments below measure them one at a time.

### Files, intervals and active streams

Increasing the number of files at a fixed row count changes per-file overhead and, depending on their intervals, the number of streams. The experiment separates a low-overlap family from an intended total-overlap family. It cannot hold every other physical property fixed: repartitioning rows among files also changes the layouts and file sizes.

At 1,200 files in the nominal depth-four layout, statistics produce 5 groups. There are 248 pairs touching at a boundary and a point shared by 5 closed intervals. Strict placement therefore needs more groups than a non-strict concatenation rule would. The pair count alone is not the proof; the simultaneous overlap is.

With these few groups, the ordered plan takes 0.417 seconds against 0.728, with 512 MB RSS. At 1,200 files both variants are slower than at twelve, but the gain remains. In the twelve-file, depth-twelve case, the ordered plan still gains 42 percent and reports 18 repartition spills in median. The added pressure appears before the time advantage disappears.

At 1,200 files with intended total overlap, the actual ordered group count is 1,196:

| Pool | Original seconds | Ordered seconds | Ordered final-aggregate spills, median |
|---|---:|---:|---:|
| 128 MB | 0.703 | 0.792 | 26 |
| 256 MB | 0.722 | 0.587 | 0 |
| 512 MB | 0.694 | 0.585 | 0 |

The ordered plan loses at the smallest budget and wins at the larger ones. The loss coincides with final-aggregate spilling. The repartition spills at every budget, about 2,275, 2,200 and 2,130 times in median, counts which the engine prints rounded, here to the nearest ten; what changes at 128 MB is the final aggregate. This is consistent with spills having an important role, but still does not isolate their contribution from the surrounding changes.

The two 1,200-file layouts compare few and many streams at the same file count. The assignment of rows to files changes between them as well, and the comparison carries both changes.

### The workaround with many groups

Raising `target_partitions` to the number of groups makes the original binary accept them. The matrix runs this up to twelve groups, where it is faster than the ordered plan and holds more memory. With more groups it stops completing: with 120 groups it completes 0 of 10 runs at 256 MB and 2 of 10 at 2 GB (3 of 10 in a second set of runs of the same configuration), and with about 1,200 groups 0 of 10 at 2 GB.

That more partitions leave less memory to each under the fair pool is in DataFusion's [configuration guide](https://github.com/apache/datafusion/blob/e1aa7d956a5aa67452c9e8bd2a033599767055d8/docs/source/user-guide/configs.md), which warns that a higher `target_partitions` can make the spilling path more frequent. The traces say what holds, in this case, at the request that ends the query. Under the fair pool the error names the ordered final aggregate: it asks 3.3 MB holding nothing, against a quota of 0.3 MB at 256 MB and of 2.4 MB at 2 GB. The pool then reports 188 and 1,558 MB reserved, most of it held by the merges of the repartition's outputs, which cannot spill and so narrow every quota. Under the greedy pool, which has no quotas, the error names those merges, and the pool is nearly full: 256 MB of 256, most of it held by the repartition. At the refusals the two pools show different conditions: quotas too small in one, overall capacity nearly exhausted in the other.

The error the query returns is the reference here. A consumer that merely ends with a refused request can have been cancelled after the error of another.

Two changes of one thing bound the failure without isolating its cause. With the grouping by statistics off and the same target, the 1,200 files are spread over the partitions without regard to their bounds and the plan is no longer ordered: at 2 GB it completes 10 of 10 runs, and at 256 MB 0 of 10. With 120 files every partition holds one file, the scan stays ordered, and the configuration is the workaround again. At 2 GB the contrast points at the ordered path, changing several operators at once; at 256 MB the number of partitions is enough for the query to fail. With the 120 ordered inputs kept and the outputs of the repartition varied, the ordered plan completes all its runs up to 8 outputs at 256 MB and at every number tried, up to 60, at 2 GB; the outputs change the partitions of the operators downstream with them.

### Many streams under a small pool

In the matrix the ordered plan with about 1,200 streams loses at 128 MB. A series at 128 MB varies the streams.

| Ordered streams | Original seconds | Ordered seconds | Ordered against original | Ordered final aggregate, peak MB |
|---:|---:|---:|---|---:|
| 150 | 0.623 | 0.625 | within the quartiles | 94.8 |
| 300 | 0.647 | 0.631 | faster | 82.9 |
| 599 | 0.656 | 0.689 | within the quartiles | 85.9 |
| 1,196 | 0.710 | 0.783 | slower | 85.9 |

The advantage of the base case is already gone at 150 streams, where the final aggregate of the ordered plan spills. The peaks of the table are medians.

The refusals of the ordered plan are above quota with the request fitting the pool, as in the base case: of those kept with about 1,200 streams, 10729 of 10762 are above quota, and in 10762 of 10762 the request fitted the pool. Most of them belong to the repartition and to the partial aggregates, which are refused while hundreds of consumers that can spill are registered and each quota is small.

The final aggregate is refused under another condition. At its refusals 2 to 5 consumers that can spill are registered, and the consumers that cannot hold 2 to 105 MB of the 128. The formula of the quota is the same; the term that weighs is the other one. Where the merges of the repartition, which cannot spill, keep most of the limit, little is left to divide among the few that can. In the base case, at the refusals of the original's final aggregate, the consumers that cannot spill hold nothing.

A larger reservation of the final aggregate is not needed for those refusals. Its peak by run with about 1,200 streams is 24.7, 82.9, 85.9, 85.9, 85.9 MB: in one run it stays near the base case, and the final aggregate is refused in that run too.

### Resident memory of many streams, without assigning it to operators

The many-stream experiment asks why the ordered plan has a much larger RSS. It varies file count, query shape, output partitions and allocator policy. Three series expose how the measurements vary on the same machine.

For Q1, which reads all columns and merges ordered streams, the excess-RSS slope is 0.34 to 0.38 MB per stream across series. A scan-only probe driven concurrently gives 0.13 to 0.16 MB per stream. The difference is not the isolated cost of merge: the consumers also drive the upstream streams differently.

The regression subtracts the baseline median at each file count. The report supplies both a conventional regression interval, conditional on those baselines, and a bootstrap which resamples both baseline and ordered runs. The latter includes baseline uncertainty, but cannot manufacture information absent from small samples or establish portability to another system.

#### Why the crossed experiment matters

At 1,196 inputs, going from two to eight outputs adds 1.4 to 1.6 GB of RSS. Dividing by the added input/output pairs gives 0.20 to 0.23 MB per pair. With input count fixed, however, output count and pair count are proportional. That division alone cannot distinguish per-output cost from per-pair cost.

The crossed experiment varies both. Across the selected small-to-large file counts, streams increase by a factor of 8.0, while the slope on output count increases by a factor of 3.8 to 7.9. The three series disagree on that second factor: at one end the slope grows much less than the streams, at the other almost as much. A pure per-output constant does not describe any of them; a pure per-pair constant describes the upper end and not the lower. This supports a scaling relationship involving inputs and outputs, without identifying the allocations behind it or settling its form.

#### What the decomposition means

An exploratory model applies the Q1 slope as a per-stream term and the two-to-eight-output difference as a per-pair term. Its residual is 181 to 488 MB. That residual is a property of the model: the part of the observed difference that its two terms leave over.

RSS is a maximum over time. Differences between process peaks may compare different phases; adding such differences does not turn them into simultaneous operator allocations. Applying an incremental per-pair coefficient to the baseline also assumes there is no separate fixed cost per input or output. The report makes those assumptions visible and compares the intermediate four-output observation with the model's prediction.

The medians of the three ordered Q3 series range from 1.8 to 2.0 GB. This describes variation between series medians, not the full range of individual executions.

#### Allocator policy

Setting `MIMALLOC_PURGE_DELAY=0` changes median Q3 RSS by -114 to -66 MB and lowers it in 3 of 3 series. This is evidence that allocator policy affects the observed residency. It neither assigns the whole residual to the allocator nor measures how much application state is live. Allocator policy and page size can interact, so the two effects, observed separately, need not add up.

### Transparent huge pages and the meaning of the peak

The THP experiment reruns the selected configurations with the machine's existing page policy and with transparent huge pages disabled for the measured process using `PR_SET_THP_DISABLE`. Query data and the plan configuration are held fixed. Five recorded runs per arm follow a warm-up run.

| Q3, 1,196 ordered streams, 256 MB pool | As configured | THP disabled |
|---|---:|---:|
| Ordered peak RSS, MB | 2081 | 596 |
| Original peak RSS, MB | 635 | 488 |
| Difference between peaks, MB | 1446 | 108 |
| Added ordered RSS from two to eight outputs, MB | 1343 | 283 |
| Ordered elapsed seconds | 0.582 | 0.603 |

Disabling THP removes most of the excess peak RSS while changing elapsed time relatively little in this case. This demonstrates a substantial effect of the page policy. Partly used huge pages provide a plausible mechanism, consistent with the kernel's documented behavior. The experiment does not map individual buffers to pages, measure unique live allocation bytes, or establish that each channel owns a separate huge page.

The remaining 108 MB is a difference between peaks under the disabled-THP policy. Both peaks can include retained allocations, stacks and other residency.

This experiment changes the interpretation of the earlier coefficients. They describe RSS in a specific allocator and page environment. The resident pages consume physical memory and can matter to external limits; what the experiment separates is residency from live application data.

### File descriptors and the timing of the peak

The open-file experiment applies process limits to the many-stream query. It asks only whether the query completes. In the recorded trials the ordered plan fails at limits through 4,096, completes at 8192 and at the larger tested limit, while the original completes already at 1024. These are observed tested limits, not an exact minimum derived by binary search.

An external sampler then observes RSS and descriptor classes through `/proc`. This observation costs CPU and its elapsed times are not compared with the ordinary timing matrix. It uses three runs per configuration; descriptor snapshots are sampled less often than RSS and may miss brief maxima.

For Q3, the recorded summary gives 4505 descriptors for the ordered plan, dominated by 4495 temporary spill files. The original has 32 descriptors. The number of Parquet files is therefore not a sufficient predictor of descriptor pressure.

The ordered peak RSS is 2094 MB, at a fraction 0.96 of process duration, with 0 data files open at the associated observation. RSS at the maximum observed number of open Parquet files is 444 MB.

The peak is late, after the sampled data-file descriptors have closed. This rules out describing that peak as simply the simultaneous cost of open readers. It does not prove that all memory originating in the scan has been released: decoded buffers may outlive file handles and remain referenced downstream. Nor does the sampler identify which descriptors are live at the instant a separately limited run fails.

### Disjoint intervals and backpressure

At depths one and two with twelve files, both builds produce two ordered scan groups. The acceptance guard is not the difference here. The experiment studies a behavior of the existing order-preserving execution path.

At depth one, the current ranges of the two producers remain disjoint. A merge can consume one producer for a while before using the other. The second producer can run ahead only as far as buffering permits. In the studied channel implementation, each input has channels to the outputs and a gate which blocks its progress when all of them hold pending data.

The matrix records 0.483 seconds at depth one against 0.404 at depth two. The dedicated experiment changes batch size and measures whole-process wall and CPU time. It runs the `accept-groups` binary throughout; with twelve files at these depths its plan has the same two scan groups as the original's. The following values are means over completed runs, not the matrix's query-time medians:

| Batch rows | Depth 1: completed, wall seconds, mean cores | Depth 2: completed, wall seconds, mean cores |
|---|---|---|
| 2,048 | 10/10, 0.536, 1.45 | 10/10, 0.418, 1.90 |
| 8,192 | 10/10, 0.490, 1.53 | 10/10, 0.403, 1.90 |
| 32,768 | 10/10, 0.401, 1.91 | 10/10, 0.404, 1.90 |
| 131,072 | 7/10, 0.706, 2.10 | 9/10, 0.728, 2.05 |

At the default batch size the accumulated repartition send duration is 0.51 seconds at depth one and 0.37 at depth two. It is more directly associated with delivery than the process-wide CPU ratio, but still does not trace who waited for whom.

Larger batches close the core-use gap at 32,768 rows. The generator's group-size imbalance remains, so unequal work alone is insufficient to explain the original gap. Counts of emitted batches are another control in the report. The layout probes with more files do not isolate the effect either: depth two at 120 files produces three groups rather than two.

Batch size is a broad intervention. It changes buffering, fixed per-batch costs and the granularity of upstream work together. The observed response supports backpressure as an explanation in combination with the channel design; it does not quantify each contribution separately.

The largest batch size has a price: at 131,072 rows, 4 of 20 runs fail, and both depths take longer among completions.

## 6. Explanations the lab does not support

Some plausible explanations are not supported by the results. Each is given here with the test that did not support it.

### The sort as what raises the pool the original needs

The query without `ORDER BY`, in which no plan sorts, slows down under the smaller pools as the deduplication does: the sort is not necessary for the slowdown. That does not clear it. By the median the original reaches a pool free of final-aggregate spills at `2g` in both queries, and the median hides the transition. At 1 GB the final aggregate of the original does not spill in 5 of 10 completed runs of the query without a sort, and in 0 of 10 of the query with one. That is compatible with a contribution of the sort in the zone of transition, for instance by competing for the pool, and does not isolate how.

### A final aggregation that needs more as the streams grow

With about 1,200 streams the ordered final aggregate reserves several times what it reserves in the base case, and the hypothesis was that this grows with the streams. In the series above it does not: from 150 to about 1,200 streams the median peak of that class does not rise, and in single runs it falls back near the base case. Where it rises between the four groups of the base case and 150 streams is not known, and the two ends of that comparison also differ in pool. The wrapper does not say what those bytes are: resident groups, retained buffers or the capacity of the structures.

### The greedy pool as a way out

The prediction was that passing from the fair pool to the greedy one would be enough for the workaround to complete. It completes 0 of 10 runs at 256 MB and 0 of 10 at 2 GB. The prediction fails; the hypothesis behind it, that the quotas of the fair pool contribute to the refusals, is supported by the traces.

### String representation and the accounting of slices

The string experiment preserves the underlying values and changes their representation. Short string views fit their bytes inline; long ones refer to external buffers. The dedicated comparison is separate from the matrix and has its own runs.

For wide strings at 128 MB, the ordered gain is 10 percent with views and 41 percent with ordinary `Utf8`. For the short-code shape, views give 43 percent. In the ordered wide-string case, reported repartition output volume drops from 594 to 61 MB when reading as `Utf8`.

The source explains a possible amplification. Hash repartition performs one `take` for an input batch, arranging rows by destination, then slices the reordered batch for outputs. Charging each slice with `get_array_memory_size()` counts the capacity of its referenced buffers. This repeats shared capacity even for non-view arrays. Views can additionally retain source string buffers shared by all fragments, making the accounted amount much larger than the rows in one fragment suggest.

This source-level fact does not establish which operator's reservation triggers the final aggregate's spills. Switching to `Utf8` changes representation, copying and operators' behavior as well as accounting.

#### The accounting-only intervention

A third binary keeps views and changes the repartition's charge to the bytes associated with each slice's rows. The receiver releases the same charge. This is an experimental policy: charging only referenced rows can differ from the capacity kept alive.

| Ordered plan, wide strings, 128 MB | Elapsed seconds | Final spills, median | Repartition spills, median |
|---|---:|---:|---:|
| Views, engine accounting | 0.627 | 14 | 8 |
| Views, slice accounting | 0.645 | 14 | 4 |
| `Utf8` | 0.405 | 0 | 0 |

The intervention affects repartition spills, so it is not behaviorally inert. Yet the final aggregate still spills the same median number of times and elapsed time is slightly worse, 0.645 seconds against 0.627, beyond the quartiles. The slice charge adds one pass over the views of every batch sent; that added work is a possible contribution to the difference, not a measured cause, and the fewer repartition spills change the run as well. For this configuration, reducing the slice reservation does not resolve final spilling. It does not support the claim that repartition's inflated accounting is the decisive cause of that spilling. It also does not prove accounting can never contribute under other budgets or workloads.

Where the remaining pressure arises is unresolved. The final aggregate's own retention or accounting of view data is a possible explanation that the lab does not establish.

Both conversion settings are needed for the plain-string comparison:

```sql
SET datafusion.execution.parquet.schema_force_view_types = false;
SET datafusion.sql_parser.map_string_types_to_utf8view = false;
```

The second prevents declared SQL string types from being mapped back to views. The original plan is affected by representation too: with views on wide strings at 128 MB it completes 8 of its 10 runs. The report includes it, while the accounting-only intervention is applied to the ordered plan.

### Plain strings as an identification

Reading the strings as `Utf8` changes outcomes. With about 1,200 streams at 128 MB the final aggregate of the ordered plan spills 0 times and the plan is faster than the original (0.586 against 0.763 seconds). For the workaround it changes little: 0 of 10 runs complete at 256 MB and 2 of 10 at 2 GB. The change moves representation, copying and retained buffers together with the accounting, so it shows that string views are involved and not which of these produces the pressure.

## 7. What the diagnosis supports upstream

None of the mechanisms named here is new to the engine. Order used by aggregations, the quotas of the fair pool, the price of more partitions and of preserving order are all documented. What the lab adds is a diagnosis of one case: where the ordering is discarded, what accepting it changes and where that stops paying, who holds the limit at the request that ends a query, and a way to repeat the measurements when the engine changes.

The acceptance guard is a demonstrated cause of the plan change in the reproducer. Removing it admits ordered groups, changes downstream execution and preserves the checked deterministic results. The lab is sufficient to diagnose that behavior and show that the rejected path can be substantially better.

It also measures circumstances where accepting all groups is worse, or requires resources beyond a process limit. That is evidence against using unconditional guard removal as the complete solution. Raising `target_partitions` is an existing workaround: in the base case it takes 0.267 seconds and 452 MB RSS, against 0.351 and 354 for the ordered plan with two downstream partitions. The workaround changes downstream parallelism along with acceptance of the ordering, so it is not a neutral control for order alone. Across the matrix, `RESULTS.md` finds it faster than the ordered plan in most of the cases where both run, and with a larger peak RSS in all of them. With many groups it stops completing, as section 5 shows. At depth twelve, where the target becomes twelve, it reaches 782 MB.

A more informed selection policy could consider the downstream consumer, effective interval overlap, input and output counts, the pool and how it divides its limit, and representation-dependent resource costs. The experiments identify those dimensions; they do not supply a portable threshold or an optimizer cost formula.

## 8. Boundaries of the evidence

This is a synthetic study on one machine and one engine commit. Outside the experiment on rows, query durations are about a second or less and per-stream fixed costs are prominent. The greedy pool appears only where an experiment changes the pool, and no data is of production scale.

The interventions on THP and accounting sharpen two explanations, and the traced runs a third. The traces keep the first and the last events of a run, with complete counters by class; the quota in them is computed by the wrapper, after the pool has decided. The batch experiment remains broader. Neither the memory model nor the external sampler measures ownership of live allocations by operator. The cost of merge comparisons is not isolated from how a merge drives its upstream streams. Small-sample bootstrap intervals do not capture every source of experimental uncertainty.

In the base layout and in the series on rows, preserving order improves execution substantially where the unordered aggregation spills. Carrying many ordered streams has time, reservation, residency and descriptor costs whose manifestation depends on the query, the pool and the environment, and they can reverse that advantage: with about 1,200 streams at 128 MB the final aggregate of the original spills in every run, at least 22 times, and the original is still the faster plan, 0.703 seconds against 0.792. The source identifies a policy decision; the experiments expose its trade-offs. Choosing a replacement policy remains engineering work.

## Reproduction

The executable workflow and prerequisites are in the repository's README and `scripts/run_lab.sh`. The script rebuilds datasets, checks plans and results, runs the experiments and generates the numerical report using recorded binary provenance.

Every number in this narrative is in `results/figures.tsv`, in the result tables or in `RESULTS.md`, as recorded at lab commit `dcf1f1e7`; none is measured apart from them.
