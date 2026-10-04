#!/usr/bin/env bash
# The whole lab in one go: datasets, plan check, the matrix, the result check
# and the tests of experiments/open-questions. Run from the repository root:
#
#   PYTHON=/path/to/python scripts/run_lab.sh results/round-N
#
# Needs bin/ with the two binaries (scripts/build_binaries.sh) and a machine
# doing nothing else. The round directory must not hold results yet; the
# experiments write into their own directories, whose earlier outputs should
# be moved to a dated subdirectory first.
set -euo pipefail
round=${1:?round directory, for example results/round-7}
py=${PYTHON:-python}
g="$py scripts/generate_partial_overlap.py"

scripts/generate_round5.sh
for f in 150 300 600; do $g --files $f --depth $f --assign rank; done      # many-streams
for d in 1 2; do $g --files 120 --depth $d --assign entity-rank; done       # depth probes

$py scripts/run_matrix.py --plan-check --out "$round"
$py scripts/run_matrix.py --runs 10 --out "$round"
$py scripts/check_results.py --out "$round/result-check"

e=experiments/open-questions
$py $e/string-view-both/run.py
$py $e/many-streams/run.py
(cd $e/many-streams && $py analyze.py > analysis.txt)
$py $e/many-streams/probes.py
$py $e/depth/batch/run.py
$py $e/depth/probes.py
$py $e/fd-limit/run.py
