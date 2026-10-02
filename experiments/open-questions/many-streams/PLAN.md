# Where the memory of the many-stream plan goes: plan

Written on 2026-10-02, before the runs below. Round 5 and the first follow-up
showed: with about 1200 ordered streams the deduplication query uses about
2 GB of process RSS, against about 0.6 GB for the original plan; with the
`ORDER BY`-only query, which has no repartition and no aggregates, the excess
is about 0.45 GB. Neither the string representation nor the batch size moves
the RSS. This plan proposes four explanations, each with a prediction that the
others do not make, and a measurement that can contradict it.

## Explanations and predictions

**S, the scan.** Every open file stream holds a fixed amount of memory
(Parquet metadata, reader state, decoded pages, the batch in flight).
Prediction: for the `ORDER BY`-only query (Q1) the excess RSS over the
original plan grows linearly with the number of streams, and does not depend
on the number of output partitions.

**P, the partial aggregates.** The ordered plan runs one partial aggregate per
input stream, each with a fixed footprint. Prediction: the per-stream slope of
the deduplication query (Q3) exceeds that of Q1, and the slope of a query with
a much smaller aggregate state (Q2, `GROUP BY` on the two sort columns) lies
between them, closer to Q1.

**R, the order-preserving repartition.** It keeps one channel per pair of
input and output partition, one spill pool per input, and in every output a
merge that holds a cursor per input. Prediction: at a fixed number of streams,
the RSS of Q3 grows with the number of output partitions (`target_partitions`
2, 4, 8), roughly in proportion to inputs times outputs.

**A, the allocator.** Part of the RSS is memory that mimalloc has freed but not
yet returned to the operating system; this would also explain the large
run-to-run spread (1.45 to 2.15 GB). Prediction: with eager purging
(`MIMALLOC_PURGE_DELAY=0`) the peak RSS falls and its spread narrows, while
the peak memory committed by the allocator, reported by
`MIMALLOC_SHOW_STATS=1`, stays about the same.

The four are not exclusive. The measurements estimate how much of the excess
each one accounts for.

## Measurements

All on the `accept-groups` binary of round 5 unless stated, fair pool of
256 MB, the same 600,000 rows redistributed into files with total overlap
(`--assign rank`, depth equal to the files), so that the ordered groups
roughly equal the files. One unrecorded warm-up per configuration, five
recorded runs, configurations interleaved. Recorded per run: peak RSS from
`/usr/bin/time`, peak commit and peak RSS from mimalloc's statistics, user and
system time, elapsed.

- **E1 (S and P).** Files 150, 300, 600 and 1200; queries Q1, Q2, Q3; the
  original plan as baseline for every file count and query (three runs).
  Fit, for each query, a straight line of the excess RSS (ordered minus the
  median of the original) against the number of ordered groups, with a 95
  percent confidence interval on the slope.
- **E2 (R).** 1200 files, Q3, `target_partitions` 2, 4 and 8.
- **E3 (A).** 1200 files, Q1 and Q3, with the default purge delay and with
  `MIMALLOC_PURGE_DELAY=0`; compare peak RSS, its spread, and peak commit.

## What would decide

- S is supported if the Q1 slope is positive with an interval away from zero,
  and E2 shows no dependence on outputs for Q1 (not run: Q1 has no
  repartition, so S is independent of outputs by construction).
- P is supported if the Q3 slope exceeds the Q1 slope with non-overlapping
  intervals, and the Q2 slope is smaller than the Q3 slope.
- R is supported if the RSS of Q3 at 1200 files rises with the outputs beyond
  the spread of the runs.
- A is supported if eager purging lowers the median peak RSS clearly and the
  peak commit does not move with it.

## Stopping rule

These three experiments, once. No further hypothesis is added after the
results; what remains unexplained is reported as such.
