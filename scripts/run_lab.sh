#!/usr/bin/env bash
# The whole lab in one go, from the binaries in bin/ to RESULTS.md.
# Run from the repository root, on a machine doing nothing else:
#
#   PYTHON=/path/to/python scripts/run_lab.sh
#
# Everything a run produces goes under results/, and RESULTS.md is generated
# from it. The script refuses to start when results/ exists: a run never
# overwrites or mixes with an earlier one. Remove results/ to run again.
#
# Steps: the machine must be configured (sudo scripts/machine_setup.sh apply;
# put it back afterwards with restore); the binaries are checked against
# provenance/binaries.sha256 (written by scripts/build_binaries.sh); the datasets are generated; the plans are
# checked; the matrix is measured; the rows the variants return are compared;
# the experiments run; every metric the engine printed is collected; the
# report is written. A failed result check or a
# failed experiment does not stop the rest; the script says what failed at
# the end and exits with status 1.
set -uo pipefail
py=$(command -v "${PYTHON:-python}") || { echo "no such interpreter: ${PYTHON:-python}" >&2; exit 2; }
case $py in /*) ;; *) py=$PWD/$py ;; esac
export PYTHON=$py
failed=()
step() { echo "== $*" >&2; "$@" || failed+=("$*"); }

if [ -e results ]; then
  echo "results/ exists: remove it to run the lab again" >&2; exit 2
fi
if ! scripts/machine_setup.sh check; then
  if [ "${ALLOW_UNCONFIGURED:-}" = 1 ]; then
    echo "the machine is not configured for the lab: running anyway, as asked" >&2
  else
    echo "configure the machine first: sudo scripts/machine_setup.sh apply" >&2
    echo "(or set ALLOW_UNCONFIGURED=1 to measure on the machine as it is)" >&2
    exit 2
  fi
fi
sha256sum -c provenance/binaries.sha256 || {
  echo "the binaries in bin/ are not those of provenance/: run scripts/build_binaries.sh" >&2; exit 2; }

# the datasets and the matrix are the base of everything else: stop if they fail
set -e
scripts/datasets.sh
$py scripts/run_matrix.py --plan-check --out results/matrix
$py scripts/run_matrix.py --runs 10 --out results/matrix
set +e

step $py scripts/check_results.py --out results/result-check
for name in string-views many-streams depth open-files huge-pages process; do
  step $py experiments/$name/run.py
done
step $py scripts/collect_metrics.py
step $py scripts/make_report.py

if ((${#failed[@]})); then
  printf 'failed: %s\n' "${failed[@]}" >&2
  exit 1
fi
