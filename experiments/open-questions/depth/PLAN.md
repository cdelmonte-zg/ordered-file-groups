# Why depth 1 is slower than depth 2: plan

Written on 2026-10-02, before the runs below, after the first probes in this
directory. Facts so far, deduplication query, 256 MB, ordered plan, two
ordered groups in both cases and the same plan shape: depth 1 takes about
0.50 s and depth 2 about 0.43 s; the CPU work is about the same (0.78 against
0.82 CPU seconds), and the process uses fewer cores at depth 1 (1.55 against
1.92); the repartition reports more time waiting to send (round 5: 524 against
387 ms).

## A correction to the first probes

The first probes rejected "serialization of disjoint partitions" because
splitting the depth-1 files by name, half to each partition, was not slower
than the interleaved split. That reasoning was wrong. At depth 1 the two
partitions read disjoint key ranges at every moment in *both* splits, so an
order-preserving merge downstream can consume from only one of them at a time
in both; the equal times are what serialization predicts. The test did not
discriminate. Likewise, smaller files keep the ranges disjoint at depth 1, so
the 120-file probe did not discriminate either.

## The mechanism the source suggests

In the order-preserving repartition every input partition has its own set of
channels, one per output, behind a gate that closes as soon as all of its
channels hold data (`repartition/distributor_channels.rs`,
`partition_aware_channels`, `Gate`). An input whose rows the merge is not
consuming can therefore run ahead by about one batch per output, then waits.

## Explanations and predictions

**H1, serialization by backpressure.** At depth 1 the merge in each output
takes rows from one input at a time, because the current key ranges of the two
inputs never overlap; the other input stops after one batch per output, so the
two scans and partial aggregates take turns. At depth 2 the ranges overlap and
both inputs advance together. Prediction: the batch size sets how far the
waiting input can run ahead. With larger batches the depth-1 process uses more
cores and the gap to depth 2 shrinks; once a batch can hold a whole file's
share (about 60,000 rows), the gap closes or nearly so. Depth 2 is affected
little.

**H2, unequal work between the two groups.** The two groups hold different
numbers of rows at depth 1, one partition finishes early and the other runs
alone at the end. Prediction: the rows per group differ more at depth 1 than
at depth 2, by enough to explain about 0.07 s; checked on the manifests
without running anything.

**H3, more and smaller batches through the repartition.** At depth 1 each sort
prefix lives in one file, so the partial aggregates complete groups and emit
more often, in smaller batches, and the repartition pays per-batch overhead.
Prediction: the partial aggregate and the repartition report clearly more
output batches at depth 1 than at depth 2; checked on the round-5 outputs.

## Measurements

- **E1 (H2):** rows per ordered group at depth 1 and 2, from the manifests and
  the groups the statistics produce.
- **E2 (H3):** output batches of the partial aggregate and of the repartition
  in the ten round-5 runs of each depth at 256 MB, ordered plan.
- **E3 (H1):** depth 1 and depth 2, `datafusion.execution.batch_size` 2048,
  8192 (the default), 32768 and 131072, ordered plan, 256 MB, six runs each
  after a warm-up, interleaved; wall time, user and system CPU time, cores
  used (CPU time divided by wall time).

## What would decide

- H1 is supported if the gap in cores between depth 1 and depth 2 falls as the
  batch size grows, and is largest at 2048.
- H2 is supported if the rows per group are clearly more unbalanced at depth 1.
- H3 is supported if depth 1 has clearly more batches in the partial aggregate
  and the repartition.

## Stopping rule

These three measurements, once. What remains unexplained is reported as such.
