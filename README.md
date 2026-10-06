# ordered-file-groups

A lab that measures what Apache DataFusion gains and what it pays when the
scan of a table made of sorted Parquet files keeps the file order by opening
more ordered file groups than `target_partitions`.

Starting point: apache/datafusion issue
[#16919](https://github.com/apache/datafusion/issues/16919). With
`split_file_groups_by_statistics = true`, overlapping sorted files can need
more ordered groups than there are partitions. The engine then rejects the
grouping and the scan loses its advertised ordering, so a deduplication query
hashes and sorts data that was already sorted.

Two builds of `datafusion-cli` 55.1.0 are compared on the same queries:

- `original`: commit `e1aa7d956` as it is;
- `accept-groups`: the same commit with one check removed
  (`patch/accept-extra-groups.patch`), so that the scan uses as many ordered
  groups as the overlap of the files requires. The patch is a measuring
  instrument, not a proposal.

A third build, `accept-groups-accounting`, adds `patch/slice-accounting.patch`
and is used by one experiment, to change the repartition's memory accounting
and nothing else.

Two more builds, `original-trace` and `accept-groups-trace`, add
`patch/trace-pool.patch`: a wrapper around the memory pool that records what
its consumers reserve and are refused. They are used by one experiment, to
attribute memory; no time of a traced run is used.

## Where things are

- `DESIGN.md`: what is measured, how, and what is predicted. No results.
- `NARRATIVE.md`: where the order of sorted files is lost, what keeping it
  changes and where it stops paying. The place to start reading.
- `RESULTS.md`: the report, generated from `results/` by
  `scripts/make_report.py`. Every table, number and comparison in it is
  computed from the recorded runs.
- `results/`: the outputs of one run of the lab. `manifests/` (the datasets),
  `matrix/` (plan check, every run's SQL, output and stderr, `results.tsv`,
  `summary.tsv`, `machine.txt`), `result-check/` (the rows the variants
  return, compared), `experiments/<name>/`, `metrics.tsv` (every metric the
  engine printed, for every operator of every run) and `figures.tsv`, the
  headline figures of the report, by name.
- `provenance/`: how the binaries were built, written by
  `scripts/build_binaries.sh`: commit, toolchain, build command, the patch as
  applied, build logs, SHA-256. The binaries themselves are not in the
  repository.
- `scripts/`: `build_binaries.sh`, the generators (`generate_base.py`,
  `generate_partial_overlap.py`, `datasets.sh`), the matrix runner with the
  plan check (`run_matrix.py`), the comparison of the rows returned
  (`check_results.py`), the collection of the engine's metrics
  (`collect_metrics.py`), the report (`make_report.py`), the configuration of
  the machine (`machine_setup.sh`) and the driver (`run_lab.sh`).
- `experiments/`: the one-variable tests (`string-views`, `many-streams`,
  `depth`, `open-files`, `huge-pages`, `large-pools`, `target-partitions`,
  `rows`, `causes`, `trace`),
  the view of the process from outside (`process`), their shared runner (`common.py`) and the code of
  their report sections (`report.py`).

The repository holds one run, and nothing in it is collected by hand.

## Reproducing

Python 3 with the packages in `requirements.txt`, a Rust toolchain, a
DataFusion checkout, and a machine doing nothing else.

    scripts/build_binaries.sh /path/to/datafusion        # bin/ and provenance/
    sudo scripts/machine_setup.sh apply                   # governor, swap; saved for restore
    rm -rf results                                        # a run never overwrites another
    PYTHON=python scripts/run_lab.sh                      # everything, RESULTS.md included
    sudo scripts/machine_setup.sh restore                 # the machine as it was

`machine_setup.sh` sets the frequency governor and the energy preference to
performance and switches the swap off; `status` prints the settings without root, and the report records
them. `run_lab.sh` refuses to measure on a machine that is not configured,
unless `ALLOW_UNCONFIGURED=1` is set. The runners pin themselves and the
processes they start to the CPUs that share the largest last-level cache, so
that on a processor with unlike cores the work does not move between them
from run to run; `LAB_CPUS=<list>` chooses other CPUs, `LAB_CPUS=all` none.

`run_lab.sh` checks the binaries against `provenance/`, generates the
datasets in `/tmp/df-16919-*`, validates the plans, measures the matrix,
compares the rows the variants return, runs the experiments and writes the
report. A failed check or experiment does not stop the rest; the script says
what failed and exits with status 1. The steps can be run one by one; each
script documents its command line, and the experiments take `--out` to write
somewhere else.

Datasets are written from fixed seeds and a fixed time origin
(2026-09-29T00:00:00Z). Every dataset holds the same 600,000 rows in files
sorted by `(col_1, col_2)`; `--depth d` makes file *i* overlap files *i+1* to
*i+d-1*, so *d* is the intended minimum number of ordered groups, and the
groups the statistics produce are in the manifest and can differ; `--shape`
renders the same integer ids at three string widths; `--duplicate-share`
copies rows; `--assign` chooses how rows go to files when there are more
files than entities. The base table cache records what it depends on beside
it and is regenerated when that differs.

## Reading the results

Two builds, one machine, one commit, synthetic data, queries under a second:
the report says what these runs show, and `DESIGN.md` lists the limits. None
of it is an argument for removing the check unconditionally. It is evidence
for choosing the number of ordered groups from the overlap, the memory budget
and the expected cost per stream, which the engine today compares with the
parallelism target only; and such a choice is only as good as the memory
accounting it relies on.

## License

MIT, see `LICENSE`.

## How this lab was made

The scripts, the patch and the documents were written with the help of an AI
assistant (Claude). The design, the choice of experiments and the conclusions
are the author's, who ran the lab and checked its findings against the
engine's source. Every number is regenerated by `scripts/run_lab.sh`.
