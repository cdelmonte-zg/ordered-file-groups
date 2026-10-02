# 16919: partial overlap sweep and re-run (2026-09-29)

## Setup

- AMD Ryzen 9 7950X3D 16-Core Processor, 32 threads, Linux 7.0.0-34-generic.
- Binaries `datafusion-cli-{original,accept-groups}-release`, 55.1.0, built 2026-09-28
  from a checkout at e1aa7d956. SHA-256 in `binaries.sha256`.
- `--mem-pool-type fair`, `--memory-limit` 128m, 256m, 512m.
- `split_file_groups_by_statistics = true`; `target_partitions = 2`, except
  `sorted-15-target10` (10).
- Ten recorded runs per dataset, variant and pool size; one unrecorded warm-up;
  alternating order. Times are the `Elapsed` value of `EXPLAIN ANALYZE`, RSS from
  `/usr/bin/time`. Medians of failed cases are computed over completed runs only
  and the number of completed runs is stated.

## Reproducing

From the DataFusion repository root, with the Python environment in
`scratch-16919/scratch-16919/.venv`:

    for d in 1 2 3 4 6 12; do
      python scratch-16919/bench-2026-09-29/generate_partial_overlap.py --depth $d
    done
    python scratch-16919/bench-2026-09-29/generate_earlier_datasets.py
    for m in 128m 256m 512m; do
      python scratch-16919/bench-2026-09-29/run_bench.py --runs 10 --memory $m --tag mem-$m
    done

Seeds and the time origin (2026-09-29T00:00:00Z) are fixed. Regenerating a dataset
gives byte-identical files (checked on depth 4). Datasets live in `/tmp/df-16919-*`.

## Files

- `results-mem-<pool>.tsv`: every run. `summary-mem-<pool>.tsv`: medians and ranges.
  `mem-<pool>/`: SQL, plans and stderr of every run.
- `fd-limit/`: 1000 files under `ulimit -n 1024` and `4096`.
- `manifests/`: rows and per-column min/max of each file of the partial datasets.
- `round-1/`: first round of the same measurements, on data generated without a
  fixed time origin. Kept for comparison, superseded by the files above.

## Open points on reproducibility

- The saved patch `experimental-accept-extra-groups.diff` contains `eprintln!` lines.
  The measured `accept-groups` binary does not contain those strings, and it does not
  contain the "falling back to unordered" message either. So the guard is removed,
  but the exact source of the binary is not the saved diff.
- The source tree state of both builds is not recorded in the binaries.

## Partial overlap datasets

Same 600,000 rows in every dataset, 12 files, each sorted by `(col_1, col_2)`.
Depth d: file i overlaps files i+1 .. i+d-1 and is disjoint from file i+d. The
minimum number of ordered groups is d. The groups produced can be more: the
algorithm starts from `target_partitions` empty groups, so depth 1 produces 2 groups
although 1 would be enough.

Overlap is built on `col_1`, because the bounds of a file are assembled from
per-column min/max, `(min col_1, min col_2)` and `(max col_1, max col_2)`.

## Elapsed seconds: median (min..max)

### 128 MB

| dataset | min groups | groups orig / mod | original | modified |
|---|---|---|---|---|
| partial-12-depth-1 | 1 | 2 / 2 | 0.506 (0.484..0.598) | 0.522 (0.515..0.535) |
| partial-12-depth-2 | 2 | 2 / 2 | 0.442 (0.413..0.470) | 0.441 (0.418..0.475) |
| partial-12-depth-3 | 3 | 2 / 3 | 0.700 (0.666..0.762) | 0.419 (0.395..0.449) |
| partial-12-depth-4 | 4 | 2 / 4 | 0.696 (0.631..0.760) | 0.391 (0.357..0.422) |
| partial-12-depth-6 | 6 | 2 / 6 | 0.687 (0.652..0.728) | 0.587 (0.555..0.631) |
| partial-12-depth-12 | 12 | 2 / 12 | 0.686 (0.646..0.714) | 0.637 (0.607..0.684) |
| sorted-medium-3 | 3 | 2 / 3 | 0.750 (0.681..0.776) | 0.409 (0.382..0.419) |
| overlap-medium-12 | 12 | 2 / 12 | 0.722 (0.704..0.783) | 0.663 (0.642..0.696) |
| sorted-15-target10 | 15 | - / 15 | fails | 0.345 (0.337..0.360) |
| overlap-1000-x-600 | 1000 | 2 / 1000 | 0.779 (0.723..0.835) | 0.827 (0.804..0.899) |

### 256 MB

| dataset | min groups | groups orig / mod | original | modified |
|---|---|---|---|---|
| partial-12-depth-1 | 1 | 2 / 2 | 0.496 (0.478..0.564) | 0.504 (0.488..0.524) |
| partial-12-depth-2 | 2 | 2 / 2 | 0.439 (0.410..0.453) | 0.432 (0.427..0.443) |
| partial-12-depth-3 | 3 | 2 / 3 | 0.667 (0.655..0.701) | 0.401 (0.377..0.433) |
| partial-12-depth-4 | 4 | 2 / 4 | 0.685 (0.656..0.705) | 0.388 (0.364..0.407) |
| partial-12-depth-6 | 6 | 2 / 6 | 0.677 (0.619..0.708) | 0.385 (0.356..0.397) |
| partial-12-depth-12 | 12 | 2 / 12 | 0.681 (0.625..0.717) | 0.360 (0.344..0.378) |
| sorted-medium-3 | 3 | 2 / 3 | 0.710 (0.673..0.763) | 0.385 (0.354..0.400) |
| overlap-medium-12 | 12 | 2 / 12 | 0.689 (0.655..0.715) | 0.343 (0.332..0.352) |
| sorted-15-target10 | 15 | - / 15 | fails | 0.349 (0.329..0.368) |
| overlap-1000-x-600 | 1000 | 2 / 1000 | 0.735 (0.699..0.779) | 0.605 (0.567..0.647) |

### 512 MB

| dataset | min groups | groups orig / mod | original | modified |
|---|---|---|---|---|
| partial-12-depth-1 | 1 | 2 / 2 | 0.504 (0.471..0.519) | 0.502 (0.475..0.518) |
| partial-12-depth-2 | 2 | 2 / 2 | 0.450 (0.435..0.474) | 0.444 (0.419..0.461) |
| partial-12-depth-3 | 3 | 2 / 3 | 0.641 (0.623..0.685) | 0.404 (0.352..0.429) |
| partial-12-depth-4 | 4 | 2 / 4 | 0.655 (0.608..0.700) | 0.380 (0.366..0.411) |
| partial-12-depth-6 | 6 | 2 / 6 | 0.673 (0.622..0.698) | 0.373 (0.347..0.409) |
| partial-12-depth-12 | 12 | 2 / 12 | 0.655 (0.595..0.706) | 0.369 (0.343..0.384) |
| sorted-medium-3 | 3 | 2 / 3 | 0.695 (0.650..0.711) | 0.381 (0.356..0.385) |
| overlap-medium-12 | 12 | 2 / 12 | 0.669 (0.625..0.709) | 0.327 (0.314..0.368) |
| sorted-15-target10 | 15 | 10 / 15 | 0.289 (0.280..0.300) | 0.340 (0.319..0.350) |
| overlap-1000-x-600 | 1000 | 2 / 1000 | 0.699 (0.686..0.754) | 0.624 (0.592..0.672) |

## Spill counts (original → modified, medians) and process RSS

| dataset | pool | sort | final aggregate | repartition | RSS MB |
|---|---|---|---|---|---|
| partial-12-depth-1 | 128m | 0 → 0 | 0 → 0 | 0 → 0 | 330 → 326 |
| partial-12-depth-1 | 256m | 0 → 0 | 0 → 0 | 0 → 0 | 324 → 322 |
| partial-12-depth-1 | 512m | 0 → 0 | 0 → 0 | 0 → 0 | 320 → 322 |
| partial-12-depth-2 | 128m | 0 → 0 | 0 → 0 | 0 → 0 | 322 → 324 |
| partial-12-depth-2 | 256m | 0 → 0 | 0 → 0 | 0 → 0 | 330 → 311 |
| partial-12-depth-2 | 512m | 0 → 0 | 0 → 0 | 0 → 0 | 317 → 325 |
| partial-12-depth-3 | 128m | 13 → 0 | 24 → 0 | 0 → 0 | 439 → 368 |
| partial-12-depth-3 | 256m | 6 → 0 | 10 → 0 | 0 → 0 | 477 → 376 |
| partial-12-depth-3 | 512m | 4 → 0 | 6 → 0 | 0 → 0 | 580 → 358 |
| partial-12-depth-4 | 128m | 13 → 0 | 24 → 0 | 0 → 6 | 442 → 402 |
| partial-12-depth-4 | 256m | 6 → 0 | 10 → 0 | 0 → 0 | 474 → 400 |
| partial-12-depth-4 | 512m | 4 → 0 | 6 → 0 | 0 → 0 | 560 → 397 |
| partial-12-depth-6 | 128m | 14 → 0 | 24 → 11 | 0 → 10 | 433 → 475 |
| partial-12-depth-6 | 256m | 6 → 0 | 10 → 0 | 0 → 2 | 487 → 434 |
| partial-12-depth-6 | 512m | 4 → 0 | 6 → 0 | 0 → 0 | 545 → 434 |
| partial-12-depth-12 | 128m | 13 → 0 | 24 → 34 | 0 → 23 | 436 → 648 |
| partial-12-depth-12 | 256m | 6 → 0 | 10 → 0 | 0 → 17 | 483 → 612 |
| partial-12-depth-12 | 512m | 4 → 0 | 6 → 0 | 0 → 3 | 534 → 597 |
| sorted-medium-3 | 128m | 14 → 0 | 24 → 0 | 0 → 3 | 408 → 354 |
| sorted-medium-3 | 256m | 8 → 0 | 10 → 0 | 0 → 0 | 462 → 348 |
| sorted-medium-3 | 512m | 4 → 0 | 6 → 0 | 0 → 0 | 528 → 341 |
| overlap-medium-12 | 128m | 16 → 0 | 24 → 40 | 0 → 24 | 445 → 630 |
| overlap-medium-12 | 256m | 7 → 0 | 10 → 0 | 0 → 24 | 493 → 692 |
| overlap-medium-12 | 512m | 4 → 0 | 6 → 0 | 0 → 18 | 538 → 668 |
| sorted-15-target10 | 128m | - → 0 | - → 123 | - → 150 | 602 → 912 |
| sorted-15-target10 | 256m | - → 0 | - → 62 | - → 150 | 741 → 960 |
| sorted-15-target10 | 512m | 15 → 0 | 20 → 0 | 0 → 150 | 799 → 942 |
| overlap-1000-x-600 | 128m | 15 → 0 | 24 → 82 | 0 → 1 | 488 → 1588 |
| overlap-1000-x-600 | 256m | 8 → 0 | 10 → 0 | 0 → 1 | 556 → 1498 |
| overlap-1000-x-600 | 512m | 4 → 0 | 6 → 0 | 0 → 1 | 610 → 1724 |

No run spilled in the partial aggregate.

## Observations

1. Control: where both binaries produce 2 groups (depth 1 and 2), plans and times
   are the same.
2. The benefit depends on three things together: the ordering that is kept, the
   number of concurrent ordered streams, and the memory budget. At 128 MB, 3 and 4
   groups remove `SortExec` and the spills of the final aggregate. At 6 and 12
   groups the final aggregate spills again and most of the gain is lost. At 256 MB
   and 512 MB the same 6 and 12 group plans do not spill in the final aggregate.
3. The modified plans move spilling, they do not remove it: sort and final
   aggregate spills go away, `RepartitionExec` (`preserve_order=true`) spills appear
   and grow with the number of groups.
4. 1000 files: slower than the original at 128 MB (ranges overlap in this round,
   separated in round 1), faster at 256 MB and 512 MB. Process RSS is about 1.5 to
   1.7 GB at every pool size, against 0.5 to 0.6 GB. Increasing the pool did not
   materially change the high process RSS. Which allocations make it up was not
   measured; that needs a heap profile.
5. 15 files, target 10: the original fails at 128 MB and 256 MB in all runs and
   completes at 512 MB, where it is faster than the modified plan. The modified plan
   completed 10 of 10 runs at 128 MB in this round and 9 of 10 in round 1
   (`SortPreservingMergeExec`, which cannot spill).
6. `ulimit -n 1024`, 1000 files, modified plan: `IO error: Too many open files
   (os error 24)`. The failing open is a temporary file, not a Parquet file. The CLI
   exits with status 0.
7. Not explained: depth 1 is slower than depth 2, with the same plan shape in both
   binaries.

## What this supports

Evidence to discuss a strategy for choosing the number of groups. Not evidence for
removing the check unconditionally.
