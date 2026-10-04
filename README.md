# ordered-file-groups

Companion lab for the article *From Sorted Files to Ordered Execution*
(cdelmonte.dev, in preparation). It measures what Apache DataFusion gains and
what it pays when the scan of a table made of sorted Parquet files keeps the
file order by opening more ordered file groups than `target_partitions`.

Starting point: apache/datafusion issue
[#16919](https://github.com/apache/datafusion/issues/16919). With
`split_file_groups_by_statistics = true`, overlapping sorted files can need
more ordered groups than there are partitions. `ListingTable` then rejects the
grouping and the scan loses its advertised ordering, so a deduplication query
hashes and sorts data that was already sorted.

Two builds of `datafusion-cli` 55.1.0 are compared on the same queries:

- `original`: commit `e1aa7d956` as it is;
- `accept-groups`: the same commit with one check removed
  (`patch/accept-extra-groups.patch`), so that the scan uses as many ordered
  groups as the overlap of the files requires.

Two further variants of the original binary run with different settings: the
workaround of the issue, `target_partitions` raised to the groups needed
(`original-target`), and the grouping by statistics switched off
(`original-split-off`).

The measurement of record is round 7, reported in `RESULTS.md`. It runs the
design of `PLAN.md`, written before round 5: one base case and six axes varied
one at a time (the query that consumes the order, the overlap depth, the
memory budget, the width of the string columns, the number of files for the
same rows, the share of duplicates), two declared crossings, hypotheses
written down beforehand and judged against the tables. Ten runs per case,
pool and variant. Round 5 (2026-10-02) is the first run of that design; rounds 6 and 7
(2026-10-04) repeat it after reviews of the scripts. All three are kept with
their reports and agree; the medians move by a few percent between rounds.

## What the measurements support

Keeping the file order removes the sort and, for a `GROUP BY` whose key
begins with the sort key, lets the aggregate close groups early; on the base
case that is 0.377 s against 0.660 s with no spill in the final aggregate at
any pool size from 128 to 512 MB, and a few small spills in the
order-preserving repartition at 128 MB. Since the original plan spills in the
final aggregate at every pool and the ordered plan never does, the round does
not separate the benefit of early emission from that of the spills avoided.
The order buys nothing measurable for a `GROUP BY` on the whole sort key at
this size, where the hash aggregate stays in memory, and nothing at all for a
`GROUP BY` without it, where the optimizer projects the ordering away, both
plans scan two byte-range groups and the times are equal. More ordered groups
cost memory and spills in the order-preserving repartition before they cost
time: twelve groups keep the gain at 256 MB, 1196 groups lose at 128 MB,
where the ordered final aggregate spills too, and win at 256 and 512 MB with
about 2 GB of RSS throughout. With strings longer than 12 bytes, the 128 MB
gain falls from 44 to 12 percent. The cause is not the width of the data but
its representation. The repartition reorders a batch with one `take`, hands
each output a slice, and reserves for every slice the full capacity of the
buffers it refers to; with Arrow string views the slices also share, and
count, every data buffer of the source batch. Read as plain strings, the same
data keeps a 43 percent gain at 128 MB (`experiments/open-questions/`): the
role of the representation is measured, the accounting mechanism comes from
the source code, and it affects the original plan too. Raising
`target_partitions` instead, the workaround, is the fastest variant in most
cases and the most memory-hungry; it loses its edge on the base data at
128 MB and at twelve groups, and stays ahead on the wide strings at 128 MB
even while spilling.

Further tests (`experiments/open-questions/`) examine the three points the
design left open. The costs they find differ in evidence and reach. The
memory of the many streams grows by 0.33 to 0.42 MB per active read stream in
the scan and by 0.20 to 0.25 MB per pair of input and output partition of the
order-preserving repartition, over three runs of the test; the second is a
coefficient derived from the increase with the outputs, and part of the
excess is memory the allocator retains. The many streams also need open
files: with 1196 streams the ordered plan fails under a limit of 4096 open
files and completes under 8192, where the original completes under 1024. Ordered groups that read disjoint key ranges at the same moment
are serialized by backpressure, which is why depth 1 is slower than depth 2,
in the unmodified binary too. And the repartition counts shared buffers once
per slice it sends, in both plans, which is the wide-string result above.

None of this is an argument for removing the check unconditionally. It is
evidence for choosing the number of ordered groups from the overlap, the
memory budget and the expected cost per stream, which the engine today
compares with the parallelism target only; and such a choice is only as good
as the memory accounting it relies on.

## Limits

One machine, one DataFusion commit, synthetic data. Every query runs under a
second on about 9 MB of Parquet, a scale at which fixed costs per stream
weigh heavily. Only the fair pool was used. The mechanisms are read in the
source and set beside the measurements; no run changed the accounting or
instrumented the reservations.

## Layout

- `PLAN.md`: the design, written before round 5.
- `RESULTS.md`: the report of round 7, generated by `scripts/make_report.py`.
- `patch/accept-extra-groups.patch`: the only difference between the two builds.
- `scripts/`: the generator (`generate_base.py`), the overlap builder
  (`generate_partial_overlap.py`), the dataset list of round 5
  (`generate_round5.sh`), the matrix runner with the plan check
  (`run_matrix.py`), the report generator (`make_report.py`),
  `build_binaries.sh` and `fd_limit_test.sh`; `run_bench.py`,
  `generate_earlier_datasets.py`, `generate_overlap_12_target_2.py` and
  `make_report_round4.py` belong to rounds 1 to 4.
- `results/round-5/`, `results/round-6/`: the earlier runs of the design
  (2026-10-02 and 2026-10-04), each with its report in `REPORT.md`.
- `results/round-7/`: the measurement of record, with `result-check/`, the
  comparison of the rows the variants return. The plans checked before
  timing (`plan-check.tsv` and one output per case and variant), every run's
  SQL, `EXPLAIN ANALYZE` output and stderr, `results.tsv` (one row per run)
  and `summary.tsv` (medians and quartiles).
- `results/round-4/`: the single-scenario rounds 1 to 4 (`REPORT.md`), with
  the build logs, toolchain and SHA-256 of the binaries used in rounds 3 to 7,
  and the open-file-limit test. The binaries are not in the repository.
- `results/round-3-rebuilt/`, `results/round-2/`, `results/round-1/`: the
  rounds of 2026-09-29 on datasets derived from the issue reporter's
  generator, which this repository does not ship.
- `results/manifests/`: per dataset, the files with rows, bytes, row groups
  and min/max of the sort key, the distinct grouping keys, the duplicate
  share and the groups the statistics produce.
- `experiments/`: two one-variable tests of 2026-10-02 (prefix cardinality,
  string width) that led to axis A4, and `experiments/open-questions/`, the
  tests on the three points the design left open, with their own plans
  written before the runs, the probes and the open-file limit; each keeps
  its earlier runs in dated subdirectories.
- `exploratory/2026-09-28/`: the first runs and single plans, superseded;
  see the README there.

## Reproducing

Python 3 with the packages in `requirements.txt`, a Rust toolchain, and a
DataFusion checkout.

    scripts/build_binaries.sh /path/to/datafusion        # two binaries in bin/
    PYTHON=python scripts/run_lab.sh results/round-8 <label>
    python scripts/make_report.py                        # reads results/round-7; set REF in the script

`run_lab.sh` generates the datasets, checks the plans, runs the matrix,
compares the rows the variants return and runs the tests of
`experiments/open-questions/`. `<label>` names the subdirectory into which
the outputs of the previous run of those tests are moved first; nothing is
overwritten. The steps can be run one by one:

    scripts/generate_round5.sh                            # eleven datasets in /tmp/df-16919-*
    python scripts/run_matrix.py --plan-check --out results/round-8
    python scripts/run_matrix.py --runs 10 --out results/round-8
    python scripts/check_results.py --out results/round-8/result-check
    python scripts/run_matrix.py --recheck-plans --out results/round-7   # validates the recorded plans, runs nothing

The plan check validates, for every case and variant, the number of
`SortExec`, the ordering the scan advertises, the scan groups, the modes of
the two aggregates and `preserve_order`. The timing runs use `EXPLAIN ANALYZE`, which returns no
rows; `check_results.py` runs each query plainly and compares the rows the
variants return. The base table cache records its parameters beside it and is
regenerated when they differ.

Datasets are written from fixed seeds and a fixed time origin
(2026-09-29T00:00:00Z); regenerating gives byte-identical files. Every
dataset holds the same 600,000 rows in files sorted by `(col_1, col_2)`;
`--depth d` makes file *i* overlap files *i+1* to *i+d-1*, so *d* is the
intended minimum number of ordered groups; the groups the statistics produce
are in the manifest and can differ (5 at 1200 files with depth 4, where the
entity-rank assignment makes 248 pairs of files touch on a boundary timestamp
and five intervals share one point; 1196 with depth 1200); `--shape` renders the same integer ids at
three string widths; `--duplicate-share` copies rows; `--assign` chooses how
rows go to files when there are more files than entities. The queries, the
variants and the matrix are in `scripts/run_matrix.py`.

## Measured on

AMD Ryzen 9 7950X3D, 32 threads, Linux 7.0.0-34, rustc 1.98.1, DataFusion
commit `e1aa7d956` (2026-09-28). Absolute times are specific to this machine.
The plan shapes follow from the commit and the settings; the spill counts
depend on the pool size and on the data and were not checked on another
machine.
