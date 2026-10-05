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
| Q5 | Q3 without its `ORDER BY` | `PartiallySorted([0, 1])` | early completion of groups; neither plan sorts |

Keeping the order changes several things at once in Q3: the scan, the sort
that disappears, the repartition that keeps the order, the mode of the
aggregate, and with them the spills. Q1 has the sort and no aggregate; Q5 has
the aggregate and no sort in either plan. The three comparisons measure
combined effects of different sets of mechanisms; they are not shares of one
another.

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

The machine as `scripts/machine_setup.sh` configures it (frequency governor
and energy preference at performance, swap off; the settings in force are
recorded with the results), with every process pinned to the CPUs that share
the largest last-level cache. The binaries of `provenance/`, `datafusion-cli`
with `--mem-pool-type
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

A third binary checks the accounting directly. It is the ordered plan with
one more patch (`patch/slice-accounting.patch`): the repartition reserves,
for every slice it sends, the bytes the slice holds for its own rows, and for
a string-view column its views plus the string bytes its rows reference,
instead of the full capacity of the buffers the slice shares with the others.
The strings stay views; only the accounting changes, at the price of one
pass over the views of every batch sent, which the engine's own accounting
does not make. If the reservations are
what makes the final aggregate spill on wide strings, the spills go with
this binary. Like the first patch it is a measuring instrument, not a
proposal: it was not checked against the engine's own memory safety.

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

E1: 150, 300, 600 and 1200 files with total overlap, ordered and original
plan, four queries: the scan alone (`SELECT *`, no merge), the `ORDER BY`
query (scan and merge), the `GROUP BY` on the sort key and the deduplication;
the excess is regressed on the ordered groups. E2: the deduplication at every
file count with 2, 4 and 8 outputs, so that streams and outputs are crossed:
a cost per output gives the same growth with the outputs at every number of
streams, a cost per pair of input and output a growth proportional to the
streams. E3: eager purging. P probes: plain `Utf8` strings, batches of 1024
rows. The peak RSS of this case moves between runs, so the whole set is
repeated in three independent series and every coefficient is reported per
series.

What this experiment can and cannot say: it measures the peak RSS of the
whole process and how that peak grows with one variable at a time. It does
not measure the memory of an operator. Peaks of different runs can fall in
different phases of the plan, so their differences are estimates, and the
sum of the estimated terms leaves a residual that is a property of the
model.

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
split between the partitions by name. Changing the batch size is a broad
intervention (it changes buffering, the granularity of the work and the
overhead per batch), so the report also gives a more direct quantity: the
`send_time` of the repartition, the time its inputs spend handing their
batches to the outputs. It sets the batch-size series beside the group
shares from the manifests and the batch counts from the matrix.

**The open-file limit (`experiments/open-files/`).** The many-stream case
with the limit on open files of the process set to several values, both
plans: which complete.

**The process from outside (`experiments/process/`).** The many-stream case
again, both plans, with `/proc/<pid>` sampled while the query runs: the
resident memory over time and the open descriptors by kind (Parquet data
files, temporary files of the spills, other). It answers what a peak alone
cannot: when the peak falls, what is open then, and how many descriptors of
which kind the ordered plan holds. Nothing in the engine is changed.

**Transparent huge pages (`experiments/huge-pages/`).** The peak RSS counts
whole pages, and where the kernel backs memory with 2 MB pages a page is
resident as soon as one byte of it is touched. A plan that spreads many small
buffers over many streams and channels can therefore show a large resident
set while touching little of it. The many-stream cases run twice, as the
machine is set and with transparent huge pages switched off for the measured
process alone (`prctl(PR_SET_THP_DISABLE)`, which needs no privilege and
changes nothing on the machine): if the excess memory of the ordered plan is
memory in use, it stays; if it is page rounding, it goes.

**Larger pools (`experiments/large-pools/`).** In the matrix the final
aggregate of the original plan spills at every pool and that of the ordered
plan at none, so the gain of the ordered plan and the spills it avoids are
not told apart. The base case runs again at 512 MB, 1, 2 and 4 GB, with the
original plan, the ordered plan and the original with the target raised. The
criterion is read at the first pool at which the median spills of the
original's final aggregate are zero. If the gain belongs to finishing groups
early, the ordered plan is still faster there beyond the quartiles; if it
belongs to the spills avoided, the two plans are within the quartiles or the
original is faster. If the original's final aggregate spills at every pool
run, the experiment decides nothing and says so. The same series runs on
the deduplication without its `ORDER BY` (Q5), in which no plan sorts: if the
two plans meet there too, the sort is not necessary for the slowdown of the
original under the smaller pools. That does not clear the sort in the query
that has one: there it can still contribute, among other things by competing
for the pool.

**Raising the target with many groups (`experiments/target-partitions/`).**
The matrix runs the workaround, the original binary with `target_partitions`
raised to the groups needed, up to twelve groups. Here it runs where the
files need 120 and about 1200 groups, at 256 MB and at 2 GB, beside the
original plan and the ordered plan with the target at two. Reported: how many
runs complete, and for those that do, time and peak RSS against the ordered
plan. A fourth configuration raises the target with
`split_file_groups_by_statistics` off: as many scan partitions, with the
files distributed without looking at their bounds. Whether that scan still
advertises the ordering depends on how the files fall into the partitions
(one file in each keeps it), so the report shows, for each configuration, the
sorts in the plan and the mode of the final aggregate. Where this plan is not
ordered, it separates two readings of the workaround's failures: if they
belong to the number of partitions under the fair pool, these runs fail too;
if they belong to the ordered path, they complete. Where it is ordered, it is
the workaround again and separates nothing.

**Causes (`experiments/causes/`).** Two observations are taken apart, one
change at a time, with ten runs per configuration.

W, the workaround that fails. The original binary with `target_partitions`
at the 120 groups of the 120 files with total overlap ends in "Resources
exhausted". Four explanations, each with a prediction the others do not make:

- the fair pool: its share for each spillable operator shrinks as the
  partitions grow. With the greedy pool, which has no shares, the runs
  complete;
- the outputs of the order-preserving repartition: it keeps a channel and a
  merge input for every pair of input and output. The ordered plan has the
  same 120 ordered inputs; with its target at 2, 8, 30 and 60 there is a
  number of outputs from which the runs stop completing;
- the string views, whose buffers are charged more than once. With plain
  `Utf8` the runs complete;
- a floor of memory for each partition, whatever the pool and the strings:
  none of the three changes makes the runs complete at 256 MB, and they
  complete at 2 GB.

More than one can hold; the report gives, for each change, how many runs
complete, and draws no conclusion the counts do not carry.

S, the ordered plan that loses with about 1200 streams at 128 MB, where its
final aggregate spills. The streams vary (150, 300, 600 and 1200 files with
total overlap, both plans, 128 MB): if the spills of the final aggregate
belong to the number of streams, they appear from some number of streams on
and the loss with them. At 1200 files the pool and the strings change as in
W: if the spills belong to the shares of the fair pool, they go with the
greedy pool; if to the string views, with plain `Utf8`. The accounting of the
repartition's slices was tested on one configuration (wide strings, 128 MB):
changing it did not remove the spills of the final aggregate there. Its
contribution elsewhere is not excluded.

## Hypotheses after the pilot of 2026-10-05

The four experiments above (larger pools, raised target, rows, causes) were
first run as a pilot, outside `results/`, and a wrapper around the memory
pool was tried on three cases (`patch/trace-pool.patch`; see below). What
follows was written after seeing that pilot. The explanations and predictions
above are kept as they were written before it. One specific prediction the
pilot already contradicts: that passing to the greedy pool is enough for the
workaround to complete. That is the prediction, not the hypothesis behind it:
that the quotas of the fair pool contribute to the refusals is, after the
pilot, supported. The
statements below are predictions for the run that follows, not predictions of
what the pilot showed.

**The instrument.** `datafusion-cli` built with `patch/trace-pool.patch`
wraps the memory pool and forwards every call unchanged. It records every
reservation as the grants and releases it saw, every refusal with what held
at that moment (who asked and how much, what it held, what the pool reported
as reserved, how many registered consumers can spill, the bytes held by those
that cannot, the bytes held by each class of consumer), whether the consumer
released memory after a refusal, and which consumers ended with a refused
request. It keeps the first events and a ring of the last ones; the events
in between are dropped, the counters by class are complete. Under a pool
without quotas the quota of an event is empty. Four limits. A release after
a refusal is what the wrapper sees; it does not see a write to disk. The
release is consistent with a spill, and the report sets the releases beside
the spills the operators report in their own metrics, as a correlation by
class and run, not as an identification of single events. A consumer that
ends with its last request refused is not thereby the one that ended the
query: it can have been cancelled after the error of another. The error the
query returns stays the reference for what failed.
The rules of the pool are those of the wrapped one, but the lock the wrapper
takes can change how the tasks interleave: every traced configuration is
compared with the untraced one on completed runs and spills, and no time of a
traced run is used. What the wrapper keeps or computes (reservations, counts,
the fair quota) is marked as such beside what it observes (the call, its
outcome, the pool's own `reserved`).

**The traced runs (`experiments/trace/`).** Every configuration runs with
the binary of the lab and with the traced one, interleaved, five runs each:
the base case with the original plan at 256 MB, 512 MB and 2 GB and with the
ordered plan at 256 MB; the workaround with 120 groups at 256 MB and 2 GB
under the fair and the greedy pool; the many-stream case at 128 MB, both
plans, both pools; the datasets of the experiment on rows, both plans, small
and large pool. The first table of the report is the control of the
instrument: completed runs and spills by operator, plain beside traced. Where
they differ, the traces of that configuration describe a run the instrument
has moved, and the report shows it. Equal completions and spills say that
the two are comparable on what was recorded; they do not show that the
wrapper perturbs nothing, and a configuration in which they differ stays
undecided between variation and an effect of the wrapper.

Three readings are fixed for the report. The peak of a class is the largest
sum of what its consumers held at one moment, as the wrapper kept it: a
reservation, not the sum of separate peaks and not resident memory. A refusal
is read against the limit as reserved plus request, not as reserved alone.
The refusals that are not above the quota the wrapper computes are listed by
class, with how near the quota they were.

**Main hypothesis.** The fair pool gives each consumer that can spill a
quota, the limit less what the others that cannot spill hold, divided by the
number of consumers that can spill. As those consumers grow in number the
quota shrinks. An operator can then be refused, and spill, or be unable to
make a first allocation, while the pool as a whole still has capacity. This
mechanism can contribute to the three observations; its standing differs.

- Base case (the original plan slows down under the smaller pools). The
  pilot observed it directly on one run: the final aggregate was refused at
  about its quota while the pool reported less than half its limit as
  reserved. Prediction for the run: in every traced run at a pool where the
  original's final aggregate spills, its refusals come with the requester's
  holding plus the request above the computed quota and with the pool's
  `reserved` below the limit; at the pools where it does not spill, it is not
  refused. What this does not show: that the whole plan would complete
  without spills within the same limit if the aggregate were allowed more.
  The other operators' reservations and peaks can change when it holds more.
  The reservations of the aggregation seen in the pilot (about 360 MB at
  2 GB) are not the need of the query.
- The workaround that fails with many groups. A plausible contribution: in
  the pilot the first refusals were requests of about 2 MB with nothing
  reserved, where the computed quota was below the request. A refusal is not
  a failure: an operator can spill or release and go on. What ends the query
  has to be read from the last events: the request refused last, what its
  consumer held, whether it had released after earlier refusals, and what the
  classes held then. Prediction: the consumers that end refused are of a
  class whose request exceeds the computed quota at that moment. If instead
  they end refused with the request within the quota, another constraint ends
  the query.
- The ordered plan that loses with many streams at 128 MB. Not yet examined
  with the instrument. Prediction if the quota contributes: the refusals of
  its final aggregate come above quota and below the limit, and they are
  fewer where the consumers that can spill are fewer (fewer streams).
  Added after the second pilot: with about 1200 streams the reservation of
  the ordered final aggregation was about four times that of the base case.
  The wrapper does not tell what those bytes are: resident groups, retained
  buffers, or the capacity of the structures. The hypothesis is that with
  many streams a larger accounted need of the final aggregation goes with
  the effect of the quotas. It is checked on the series of streams that is
  already there (150, 300, 600 and about 1200, same rows and the same two
  final partitions), which the traced runs now include: if it holds, the
  peak of that class grows with the streams.

**The greedy pool as the next control.** It has no quotas. If under it the
refusals above quota and below the limit go away and the query fails
elsewhere, the limitation by quota is separated from a second constraint of
memory; that is not a refutation of the first. The traced runs say which
class is refused under the greedy pool and with how much reserved. In the
pilot the two pools showed different conditions at the moment of the
refusals: individual quotas too small under the fair pool, where the
consumers that cannot spill also held memory that narrows those quotas, and
an overall capacity nearly exhausted under the greedy one, with the
repartition as the main holder. What remains is to connect those conditions
to the error that ends the query.

**What stays one change among several.** The outputs of the repartition
change the partitions of the operators downstream with them; plain `Utf8`
changes copies and retained buffers together with the accounting. For both,
the traced runs give the reservations by class beside the outcome, and no
conclusion is drawn from the outcome alone.

**Rows.** The experiment on rows above does not identify why the ordered plan
holds less: more rows can mean more prefixes, more distinct groups under each
prefix, or more duplicates, and each predicts something different for a
partially ordered aggregation. It is to be replaced by two series with the
streams and the width of the rows fixed: more prefixes with the same groups
under each, and more groups under each prefix with the same prefixes. The
prediction for the first is a reservation of the ordered aggregation that
stays level; for the second, one that grows. A second pilot ran both series
with the traced binaries and did not see the growth predicted for the
second: the reservation stayed level in both. The prediction is kept as
written. In the range explored it was not observed, and that range was
narrow: the keys per prefix of the manifests are means, and even the largest
prefix held a few hundred keys. One dataset is therefore added, with the keys
of 6 million rows concentrated under a few hundred prefixes (tens of
thousands of keys each; the distinct keys in all come out fewer, because the
combinations under one prefix are bounded). There the growth, if it is there,
should be visible. That batches and capacity already allocated dominate the
reservation at a few hundred keys is a possible reason for the level
reservation, not a finding. Means, medians and largest prefixes are counted
on the source table, not on what reaches each partition of the final
aggregate. The manifests give, for every dataset, the distinct prefixes and the keys
under each. The pilot kept the generator as it was, which bounds the prefixes
by its timestamp clusters: it had varied the keys under each prefix and not
the prefixes. Three things are reported apart: whether a plan completes under
the small pool, what its aggregation reserves (the traced runs), and what
the process holds resident.

## Limits

One machine, one DataFusion commit, synthetic data, sub-second queries on a
few MB of Parquet outside the experiment on rows, the fair pool outside the
experiment on causes. Ten runs per cell of the matrix and of the experiments on
string views, on depth, on the larger pools, on the raised target and on the causes; five for the experiment on rows; five per cell and series for the ordered plan in
the memory of the many streams, three for its baselines and probes; five per
arm for the huge pages; three for the open-file limit and for the process
seen from outside: the separation of the quartiles used in the report is a descriptive
criterion, not a test of significance, and the three series show the
variability of one environment, not how far a result carries to other
machines or loads. Where runs fail there are two results, how many complete
and how long those take, and the report gives both. Except for the slice
accounting, the mechanisms are read in the source and set beside the
measurements; the occupancy of the channels is not instrumented.
