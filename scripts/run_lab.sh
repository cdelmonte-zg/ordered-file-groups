#!/usr/bin/env bash
# The whole lab in one go: datasets, plan check, the matrix, the result check
# and the tests of experiments/open-questions. Run from the repository root:
#
#   PYTHON=/path/to/python scripts/run_lab.sh results/round-N <label>
#
# <label> names the subdirectory into which the outputs of the previous run of
# the experiments are moved before they run again, for example the date of
# that run. Needs bin/ with the two binaries (scripts/build_binaries.sh) and a
# machine doing nothing else. The round directory must not hold results yet.
#
# A failed result check or a failed experiment does not stop the rest; the
# script reports what failed at the end and exits with status 1.
set -uo pipefail
round=${1:?round directory, for example results/round-7}
label=${2:?label for the outputs of the previous run of the experiments, for example its date}
py=$(command -v "${PYTHON:-python}") || { echo "no such interpreter: ${PYTHON:-python}" >&2; exit 2; }
case $py in /*) ;; *) py=$PWD/$py ;; esac
export PYTHON=$py
g="$py scripts/generate_partial_overlap.py"
e=experiments/open-questions
failed=()
step() { echo "== $*" >&2; "$@" || failed+=("$*"); }

# refuse before anything is touched: a round that exists, a label that is taken
if [ -e "$round/results.tsv" ] || [ -e "$round/plan-check.tsv" ]; then
  echo "$round already holds a round: use a new directory" >&2; exit 2
fi
$py $e/archive.py --check "$label" || exit 2

# the datasets and the matrix are the base of everything else: stop if they fail
set -e
$py $e/archive.py "$label"
scripts/generate_round5.sh
for f in 150 300 600; do $g --files $f --depth $f --assign rank; done      # many-streams
for d in 1 2; do $g --files 120 --depth $d --assign entity-rank; done       # depth probes
$py scripts/run_matrix.py --plan-check --out "$round"
$py scripts/run_matrix.py --runs 10 --out "$round"
set +e

step $py scripts/check_results.py --out "$round/result-check"
step $py $e/string-view-both/run.py
step $py $e/many-streams/run.py
if $py $e/many-streams/analyze.py > $e/many-streams/analysis.txt.partial; then
  mv $e/many-streams/analysis.txt.partial $e/many-streams/analysis.txt
else
  rm -f $e/many-streams/analysis.txt.partial; failed+=("many-streams/analyze.py")
fi
step $py $e/many-streams/probes.py
step $py $e/depth/batch/run.py
step $py $e/depth/probes.py
step $py $e/fd-limit/run.py

if ((${#failed[@]})); then
  printf 'failed: %s\n' "${failed[@]}" >&2
  exit 1
fi
