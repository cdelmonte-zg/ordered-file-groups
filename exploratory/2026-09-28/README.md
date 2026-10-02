# Exploratory runs of 2026-09-28

First look at the problem, kept for provenance. These runs used binaries built
on 2026-09-28 from a checkout at e1aa7d956 whose exact patch state is not
recorded (the saved diff `experimental-accept-extra-groups.diff` carries
`eprintln!` calls that the measured binaries did not have), three runs per
case, and datasets generated without a fixed time origin. The figures quoted in
the issue comment of 2026-09-29 ("about 9% slower", "almost three times the
peak RSS") come from here. They are superseded by `results/round-3-rebuilt/`,
which repeats every case with rebuilt binaries, ten runs and reproducible data.

- The data of these runs came from the generator attached to the issue by its
  reporter (https://gist.github.com/zheniasigayev/2e5e471c9070cfa685d938bced47aa7f),
  modified so that every file is sorted before it is written: the original
  left some entities sorted by `col_2` descending (inversion at row 42030 of
  the first file), which is the first finding of the comment of 2026-09-29.
  Neither the gist nor the modified copy is part of this repository.
- `bench_datafusion_16919.sh`, `bench-release/`: three runs on the 3-file and
  the 12-file overlapping datasets.
- `bench_datafusion_16919_scale.sh`, `bench-scale/overlap-1000-x-600/`: the
  1000-file case, three runs.
- `repro_*.sql`, `plan-15-*.out`, `target10-*`, `plans/`: the single plans and
  logs looked at while narrowing the mechanism down to the group-count check in
  `ListingTable`.
