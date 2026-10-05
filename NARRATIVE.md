# Reading the ordered-file-groups experiments

*Why each experiment exists, what it establishes, and where the explanation stops.*

This document explains the reasoning behind the lab. [DESIGN.md](DESIGN.md) records the experimental design; [RESULTS.md](RESULTS.md) contains the generated tables and comparisons; the [recorded outputs](results/) support both. The numbers come from the results recorded at lab commit `e15d1d0d`, on DataFusion commit `e1aa7d956`.

The numbers below are generated from the lab's recorded results rather than copied by hand. Regenerating them is a consistency check, not an editorial review: when an experiment changes its outcome, the interpretation must be read again.

## 1. The question and the intervention

Sorted Parquet files do not automatically produce a sorted scan. Concatenating two files preserves an ascending order only when their boundary values permit it. Overlapping files may require separate streams, which can then be merged.

DataFusion uses file groups as scan partitions. In the path studied here, it attempts to build ordered groups from statistics, starting with `target_partitions` empty groups. Files are considered by increasing minimum; a file is appended to the least populated group that is empty or whose last file's maximum is strictly below the file's minimum. When no group qualifies, another one is added. These statistics establish whether files can be concatenated; they do not prove that the rows within each file are sorted. The lab declares an order which its generator actually writes.

At the studied commit, the listing-table code accepts the proposed groups only when their count does not exceed the target. If it rejects them, it retains the initial grouping; a later optimizer step can redistribute files by size and split them into byte ranges. The resulting scan may no longer advertise an order.

The base dataset has 600,000 rows in twelve files sorted by `(col_1, col_2)`. Its bounds require four ordered groups while the configured target is two. This makes the guard decisive. The two primary builds differ only in whether that guard remains:

| Variant | Intervention | Purpose |
|---|---|---|
| `original` | Unmodified commit, statistics grouping enabled, target two | Observe the rejected grouping and subsequent plan |
| `accept-groups` | Remove the guard, keep target two | Measure the whole effect of accepting extra ordered groups |
| `original-target` | Original build, target raised to the required count | Compare the existing configuration workaround |
| `original-split-off` | Original build, statistics grouping disabled | Base-case reference for the grouping setting |

The primary intervention is small in source code, but broad in execution. It changes scan streams, aggregate modes, repartition behavior and the presence of a sort. Its effect is the combined consequence of those changes. It is not an isolated measurement of one operator, and the patch is not an unconditional fix proposal.

The practical question is whether useful downstream work saved by order outweighs the resources needed to carry that order through the plan. Time and memory are separate outcomes: a faster plan can be more expensive to keep resident.

## 2. What is controlled and what is checked

The machine is a Ryzen 9 7950X3D. Governor and energy preference are set to `performance`, boost remains enabled, swap is disabled, and the measured processes are pinned to the eight physical cores sharing the larger cache, including their SMT siblings. This controls placement and power policy; it does not lock the processor to one frequency. The recorded machine state is authoritative.

The matrix uses the fair memory pool at 128, 256 and 512 MB. Each configuration has a warm-up and ten recorded runs, with variant order rotated. The many-stream experiment uses five ordered runs and three baseline runs per cell in each of three series. The depth and string experiments use ten runs; the open-file tests and external process observations use three. Repetition across series explores variation on this machine, not portability across machines.

The matrix records 620 executions, 2 of which fail, in one configuration: the original plan on wide strings at 128 MB. Failed runs are retained and excluded from timing summaries. The separate correctness check passes 62 of 62 cells. It compares multisets of deterministic columns, checks the requested sort key, and for deduplication checks the number of distinct keys against the manifest. Unordered `first_value` results may differ legitimately and are excluded. Rows tied on the requested sort key need not appear in an identical sequence.

The comparison queries are executed through a `COPY` sink so that collecting terminal output does not consume the query's pool. The plan beneath that sink is checked against the timed plan's relevant properties. Correctness is therefore a separate check of comparable execution plans, not a comparison of rows emitted by `EXPLAIN ANALYZE` itself.

Three kinds of time must remain distinct:

- The matrix's CLI `Elapsed` includes planning and query execution for the measured statement, including listing and statistics work involved in constructing the scan.
- Whole-process wall time and user/system CPU time come from `/usr/bin/time`. The depth experiment uses their ratio as average cores used.
- Operator metrics are accumulated durations. `elapsed_compute` times instrumented computation; `send_time` includes delivery and waiting. Neither should be added mechanically to the query's elapsed time, nor treated as a direct sample of per-operator CPU consumption.

RSS is the process's resident memory, not its memory-pool reservation. `output_bytes` is cumulative Arrow-accounted batch volume, not unique allocated bytes or peak resident memory. Shared buffers may be counted repeatedly. Counts printed with suffixes must also be parsed as scaled values: `2.12 K` means approximately 2,120, not two; the printed value itself has limited precision.

The report calls one variant faster when its third quartile lies below the other's first. This is a declared descriptive rule, not a hypothesis test or a guarantee of statistical significance. For configurations with failures, completion frequency and time conditional on completion must both be considered.

`DESIGN.md` states a prediction for each axis of the matrix before the run, and `RESULTS.md` checks each one against a declared rule in its table of predictions. Several do not hold. This narrative follows the results, not the predictions.

## 3. Establishing the benefit before explaining it

Q3 groups by six columns, including the two-column sort prefix, and orders its output by that prefix. In the ordered plan, a change of prefix proves that the previous prefix will not return in that stream. The aggregate can finish those groups early. Partial aggregation still emits contributions which must be combined after redistribution; local completion is not completion across the entire query.

At 256 MB, Q3 changes from 0.609 seconds to 0.356, a reduction of 42 percent. The original spills in its sort and final aggregate; the ordered base case does not spill. This establishes a useful effect for the dataset and configuration. It does not yet explain how much comes from removing the sort, completing prefixes early, or avoiding final-aggregate spills.

The original build with statistics grouping disabled (`original-split-off`) takes 0.600 seconds, within the quartiles of the original. The setting alone does not move the original plan.

The operator measurements make the next question more precise:

| Metric, accumulated seconds | Original | Ordered |
|---|---:|---:|
| Sort `elapsed_compute` | 0.029 | absent |
| Final aggregate `elapsed_compute` | 0.880 | 0.375 |
| Partial aggregate `elapsed_compute` | 0.274 | 0.318 |
| Repartition `send_time` | 0.237 | 0.834 |

The largest reduction in recorded compute duration is at the final aggregate. The repartition spends longer delivering batches in the ordered plan. That increase can include backpressure and does not isolate the comparisons performed by the merge.

### Removing the sort from the question

Q5 is the same deduplication without the final `ORDER BY`. Neither plan sorts, but the ordered plan still takes 0.307 seconds against 0.555. The gain persists without a sort to remove. Together with the operator metrics, this supports an important role for aggregation, while leaving changes in the scan and repartition coupled to it. It does not yield additive shares of the Q3 speedup.

### Queries which use order differently

| Query | Original seconds | Ordered seconds | What the comparison establishes |
|---|---:|---:|---|
| Q1, only `ORDER BY` | 0.049 | 0.026 | An ordered scan and merge can beat the scan-and-sort plan |
| Q2, group on the complete sort key | 0.049 | 0.054 | `Sorted` mode does not ensure a time benefit when the hash alternative fits in memory |
| Q4, group without the sort key | 0.014 | 0.014 | The optimizer discards the unused ordering; the measured times overlap |

Q1 is the most direct sort-versus-merge case, but its scan organization changes too. Q2 prevents the argument from becoming "ordered aggregation is always faster." Q4 checks whether the scan intervention creates a benefit when the query has no use for the property. It does not prove a universal necessity theorem; it is the negative control for this plan and workload.

## 4. Memory budgets and duplicates: useful controls, limited attribution

The budget series asks whether increasing available pool memory changes the gap. Across 128, 256 and 512 MB the base-case reductions are 43 / 42 / 45 percent. The ordered final aggregate does not spill at these budgets, while the original does. This series therefore does not remove spill avoidance as an explanation: there is no observed budget at which both final aggregates are spill-free.

| Pool | Original peak RSS, MB | Ordered peak RSS, MB | Original sort spills, median |
|---|---:|---:|---:|
| 128 MB | 444 | 370 | 15 |
| 256 MB | 496 | 355 | 7 |
| 512 MB | 527 | 351 | 4 |

The original's spill count falls without a corresponding transformation in elapsed time. That weakens a simple proportional relationship between spill count and query duration; it does not show that spills are unimportant. A spill count says neither how much data moved nor how its work overlaps with other work.

The duplicate experiment keeps the total row count and changes the number of distinct grouping keys. With roughly half the rows duplicated, elapsed time becomes 0.379 seconds for the original and 0.190 for the ordered plan, a 50 percent reduction. Both benefit from fewer distinct keys. The ordered plan benefits proportionally more, but this experiment does not isolate prefix completion as the reason: aggregate state, reductions in rows and spill volumes change together.

These controls are recorded because they constrain explanations; neither carries a causal claim of its own.

## 5. Files, intervals and active streams

Increasing the number of files at a fixed row count changes per-file overhead and, depending on their intervals, the number of streams. The experiment separates a low-overlap family from an intended total-overlap family. It cannot hold every other physical property fixed: repartitioning rows among files also changes the layouts and file sizes.

At 1,200 files in the nominal depth-four layout, statistics produce 5 groups. There are 248 pairs touching at a boundary and a point shared by 5 closed intervals. Strict placement therefore needs more groups than a non-strict concatenation rule would. The pair count alone is not the proof; the simultaneous overlap is.

With these few groups, the ordered plan takes 0.413 seconds against 0.718, with 498 MB RSS. At 1,200 files both variants are slower than at twelve, but the gain remains. In the twelve-file, depth-twelve case, the ordered plan still gains 44 percent and reports 17.5 repartition spills in median. The added pressure appears before the time advantage disappears.

At 1,200 files with intended total overlap, the actual ordered group count is 1,196:

| Pool | Original seconds | Ordered seconds | Ordered final-aggregate spills, median |
|---|---:|---:|---:|
| 128 MB | 0.713 | 0.778 | 28 |
| 256 MB | 0.716 | 0.578 | 0 |
| 512 MB | 0.696 | 0.589 | 0 |

The ordered plan loses at the smallest budget and wins at the larger ones. The loss coincides with final-aggregate spilling. The repartition spills at every budget, about 2,280, 2,205 and 2,120 times in median, counts which the engine prints rounded, here to the nearest ten; what changes at 128 MB is the final aggregate. This is consistent with spills having an important role, but still does not isolate their contribution from the surrounding changes.

The two 1,200-file layouts are a useful comparison of few versus many streams with the same file count. They are not a pure intervention on stream count: row assignment changes too. "Streams, not files" would overstate what this comparison identifies.

## 6. Explaining resident memory without assigning it to operators

The many-stream experiment asks why the ordered plan has a much larger RSS. It varies file count, query shape, output partitions and allocator policy. Three series expose how the measurements vary on the same machine.

For Q1, which reads all columns and merges ordered streams, the excess-RSS slope is 0.34 to 0.39 MB per stream across series. A scan-only probe driven concurrently gives 0.15 to 0.17 MB per stream. The difference is not the isolated cost of merge: the consumers also drive the upstream streams differently.

The regression subtracts the baseline median at each file count. The report supplies both a conventional regression interval, conditional on those baselines, and a bootstrap which resamples both baseline and ordered runs. The latter includes baseline uncertainty, but cannot manufacture information absent from small samples or establish portability to another system.

### Why the crossed experiment matters

At 1,196 inputs, going from two to eight outputs adds 1.3 to 1.4 GB of RSS. Dividing by the added input/output pairs gives 0.19 to 0.20 MB per pair. With input count fixed, however, output count and pair count are proportional. That division alone cannot distinguish per-output cost from per-pair cost.

The crossed experiment varies both. Across the selected small-to-large file counts, streams increase by a factor of 8.0, while the slope on output count increases by a factor of 4.8 to 5.9. Neither a pure per-output constant nor a pure per-pair constant describes the point estimates by itself. This supports a scaling relationship involving inputs and outputs, without identifying the allocations behind it or formally rejecting all simpler models under uncertainty.

### What the decomposition means

An exploratory model applies the Q1 slope as a per-stream term and the two-to-eight-output difference as a per-pair term. Its residual is 445 to 476 MB. That residual is a property of the model. It is not an independently observed block of unattributed memory.

RSS is a maximum over time. Differences between process peaks may compare different phases; adding such differences does not turn them into simultaneous operator allocations. Applying an incremental per-pair coefficient to the baseline also assumes there is no separate fixed cost per input or output. The report makes those assumptions visible and compares the intermediate four-output observation with the model's prediction.

The medians of the three ordered Q3 series range from 1.9 to 2.0 GB. This describes variation between series medians, not the full range of individual executions.

### Allocator policy

Setting `MIMALLOC_PURGE_DELAY=0` changes median Q3 RSS by -336 to -63 MB and lowers it in 3 of 3 series. This is evidence that allocator policy affects the observed residency. It neither assigns the whole residual to the allocator nor measures how much application state is live. Allocator policy and page size can interact, so their separately observed effects should not be added as independent components.

## 7. Transparent huge pages: an intervention on the meaning of the peak

The THP experiment reruns the selected configurations with the machine's existing page policy and with transparent huge pages disabled for the measured process using `PR_SET_THP_DISABLE`. Query data and the plan configuration are held fixed. Five recorded runs per arm follow a warm-up run.

| Q3, 1,196 ordered streams, 256 MB pool | As configured | THP disabled |
|---|---:|---:|
| Ordered peak RSS, MB | 1836 | 602 |
| Original peak RSS, MB | 642 | 496 |
| Difference between peaks, MB | 1194 | 106 |
| Added ordered RSS from two to eight outputs, MB | 1618 | 313 |
| Ordered elapsed seconds | 0.582 | 0.604 |

Disabling THP removes most of the excess peak RSS while changing elapsed time relatively little in this case. This demonstrates a substantial effect of the page policy. Partly used huge pages provide a plausible mechanism, consistent with the kernel's documented behavior. The experiment does not map individual buffers to pages, measure unique live allocation bytes, or establish that each channel owns a separate huge page.

The remaining 106 MB is a difference between peaks under the disabled-THP policy. It must not be relabeled as the additional live memory of ordered execution. Both peaks can include retained allocations, stacks and other residency.

This experiment changes the interpretation of the earlier coefficients. They describe RSS in a specific allocator and page environment, not intrinsic byte costs of a stream or a channel pair. The resident pages still consume physical memory and can matter to external limits; the distinction is between residency and live application data, not real and imaginary memory.

## 8. File descriptors and the timing of the peak

The open-file experiment applies process limits to the many-stream query. It asks whether the query completes, not how fast it is. In the recorded trials the ordered plan fails at limits through 4,096, completes at 8192 and at the larger tested limit, while the original completes already at 1024. These are observed tested limits, not an exact minimum derived by binary search.

An external sampler then observes RSS and descriptor classes through `/proc`. This observation costs CPU and its elapsed times are not compared with the ordinary timing matrix. It uses three runs per configuration; descriptor snapshots are sampled less often than RSS and may miss brief maxima.

For Q3, the recorded summary gives 4411 descriptors for the ordered plan, dominated by 4401 temporary spill files. The original has 32 descriptors. The number of Parquet files is therefore not a sufficient predictor of descriptor pressure.

The ordered peak RSS is 2005 MB, at a fraction 0.97 of process duration, with 0 data files open at the associated observation. RSS at the maximum observed number of open Parquet files is 411 MB.

The peak is late, after the sampled data-file descriptors have closed. This rules out describing that peak as simply the simultaneous cost of open readers. It does not prove that all memory originating in the scan has been released: decoded buffers may outlive file handles and remain referenced downstream. Nor does the sampler identify which descriptors are live at the instant a separately limited run fails.

## 9. Disjoint intervals and backpressure

At depths one and two with twelve files, both builds produce two ordered scan groups. The acceptance guard is not the difference here. The experiment studies a behavior of the existing order-preserving execution path.

At depth one, the current ranges of the two producers remain disjoint. A merge can consume one producer for a while before using the other. The second producer can run ahead only as far as buffering permits. In the studied channel implementation, each input has channels to the outputs and a gate which blocks its progress when all of them hold pending data.

The matrix records 0.479 seconds at depth one against 0.397 at depth two. The dedicated experiment changes batch size and measures whole-process wall and CPU time. It runs the `accept-groups` binary throughout; with twelve files at these depths its plan has the same two scan groups as the original's. The following values are means over completed runs, not the matrix's query-time medians:

| Batch rows | Depth 1: completed, wall seconds, mean cores | Depth 2: completed, wall seconds, mean cores |
|---|---|---|
| 2,048 | 10/10, 0.528, 1.44 | 10/10, 0.415, 1.88 |
| 8,192 | 10/10, 0.488, 1.54 | 10/10, 0.403, 1.91 |
| 32,768 | 10/10, 0.395, 1.94 | 10/10, 0.395, 1.94 |
| 131,072 | 7/10, 0.706, 2.11 | 6/10, 0.728, 2.04 |

At the default batch size the accumulated repartition send duration is 0.51 seconds at depth one and 0.36 at depth two. It is more directly associated with delivery than the process-wide CPU ratio, but still does not trace who waited for whom. It must not be added to elapsed time.

Larger batches close the core-use gap at 32,768 rows. The generator's group-size imbalance remains, so unequal work alone is insufficient to explain the original gap. Counts of emitted batches are another control in the report. The layout probes with more files do not isolate the effect either: depth two at 120 files produces three groups rather than two.

Batch size is a broad intervention. It changes buffering, fixed per-batch costs and the granularity of upstream work together. The observed response supports backpressure as an explanation in combination with the channel design; it does not quantify each contribution separately.

At 131,072 rows, 7 of 20 runs fail. Both depths take longer among completions. This prevents interpreting a vanishing core gap as an unconditional recommendation to increase batches.

## 10. String representation and a rejected explanation

The string experiment preserves the underlying values and changes their representation. Short string views fit their bytes inline; long ones refer to external buffers. The dedicated comparison is separate from the matrix and has its own runs: its figures must not be mixed with matrix medians merely because the configuration names resemble one another.

For wide strings at 128 MB, the ordered gain is 13 percent with views and 41 percent with ordinary `Utf8`. For the short-code shape, views give 44 percent. In the ordered wide-string case, reported repartition output volume drops from 593 to 61 MB when reading as `Utf8`.

The source explains a possible amplification. Hash repartition performs one `take` for an input batch, arranging rows by destination, then slices the reordered batch for outputs. Charging each slice with `get_array_memory_size()` counts the capacity of its referenced buffers. This repeats shared capacity even for non-view arrays. Views can additionally retain source string buffers shared by all fragments, making the accounted amount much larger than the rows in one fragment suggest.

This source-level fact does not establish which operator's reservation triggers the final aggregate's spills. Switching to `Utf8` changes representation, copying and operators' behavior as well as accounting.

### The accounting-only intervention

A third binary keeps views and changes the repartition's charge to the bytes associated with each slice's rows. The receiver releases the same charge. This is an experimental policy, not a proposed exact accounting system for the lifetime of shared allocations: charging only referenced rows can differ from the capacity kept alive.

| Ordered plan, wide strings, 128 MB | Elapsed seconds | Final spills, median | Repartition spills, median |
|---|---:|---:|---:|
| Views, engine accounting | 0.626 | 14 | 8 |
| Views, slice accounting | 0.639 | 14 | 4 |
| `Utf8` | 0.406 | 0 | 0 |

The intervention affects repartition spills, so it is not behaviorally inert. Yet the final aggregate still spills the same median number of times and elapsed time is slightly worse, 0.639 seconds against 0.626, beyond the quartiles: the slice charge costs one more pass over the views of every batch sent. For this configuration, reducing the slice reservation does not resolve final spilling. It does not support the claim that repartition's inflated accounting is the decisive cause of that spilling. It also does not prove accounting can never contribute under other budgets or workloads.

Where the remaining pressure arises is unresolved. The final aggregate's own retention or accounting of view data is a possible explanation, not an established finding. The result is not a general recommendation to disable views.

Both conversion settings are needed for the plain-string comparison:

```sql
SET datafusion.execution.parquet.schema_force_view_types = false;
SET datafusion.sql_parser.map_string_types_to_utf8view = false;
```

The second prevents declared SQL string types from being mapped back to views. The original plan is affected by representation too: with views on wide strings at 128 MB it completes 9 of its 10 runs. The report includes it, while the accounting-only intervention is applied to the ordered plan.

## 11. What the diagnosis supports upstream

The acceptance guard is a demonstrated cause of the plan change in the reproducer. Removing it admits ordered groups, changes downstream execution and preserves the checked deterministic results. The lab is sufficient to diagnose that behavior and show that the rejected path can be substantially better.

It also measures circumstances where accepting all groups is worse, or requires resources beyond a process limit. That is evidence against using unconditional guard removal as the complete solution. Raising `target_partitions` is an existing workaround: in the base case it takes 0.263 seconds and 444 MB RSS, against 0.356 and 355 for the ordered plan with two downstream partitions. The workaround changes downstream parallelism along with acceptance of the ordering, so it is not a neutral control for order alone. Across the matrix, `RESULTS.md` finds it faster than the ordered plan in most of the cases where both run, and with a larger peak RSS in all of them. At depth twelve, where the target becomes twelve, it reaches 778 MB.

A more informed selection policy could consider the downstream consumer, effective interval overlap, input and output counts, available budget and representation-dependent resource costs. The experiments identify those dimensions; they do not supply a portable threshold or an optimizer cost formula.

## 12. Boundaries of the evidence

This is a synthetic, small-scale study on one machine, one engine commit and the fair pool. Query durations are mostly below a second and per-stream fixed costs are prominent. The measured configurations do not include a spill-free original final aggregate for the main budget series, a greedy-pool comparison, or production-scale data.

The interventions on THP and accounting sharpen two explanations. The batch experiment remains broader. Neither the memory model nor the external sampler measures ownership of live allocations by operator. The cost of merge comparisons is not isolated from how a merge drives its upstream streams. Small-sample bootstrap intervals do not capture every source of experimental uncertainty.

The useful stopping point is a bounded answer: preserving order can improve execution substantially, but carrying many ordered streams has time, residency and descriptor costs whose manifestation depends on the query and environment. The source identifies a policy decision; the experiments expose its trade-offs. Choosing a replacement policy remains engineering work.

## Reproduction and numerical maintenance

The executable workflow and prerequisites remain in the repository's README and `scripts/run_lab.sh`. This narrative does not duplicate those commands or change the run protocol. The script rebuilds datasets, checks plans and results, runs the experiments and generates the numerical report using recorded binary provenance.

The numbers in this narrative come from `results/figures.tsv`, selected cells of the result tables and `results/metrics.tsv`. When the lab is run again they are generated again, and a changed value is a reason to reread the sentence around it, not only to replace the number.

Keep one authoritative location for each kind of information: the lab data for measurements, the source and patch for implementation facts, and this narrative for experimental interpretation.
