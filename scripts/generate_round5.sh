#!/usr/bin/env bash
# The thirteen datasets of PLAN.md (round 5). Run from the repository root.
set -euo pipefail
py=${PYTHON:-python}
g="$py scripts/generate_partial_overlap.py"
rm -rf /tmp/df-16919-base
for d in 1 2 4 12; do $g --depth $d; done                     # A2, base at depth 4 (S1, entity)
$g --depth 4 --shape S0                                        # A4
$g --depth 4 --shape S2                                        # A4
$g --depth 4 --duplicate-share 0.5                             # A6
for f in 120 1200; do                                          # A5; the 12-file points are depth 4 and depth 12 above
  $g --files $f --depth 4  --assign entity-rank                 # groups stay 4, files hold one entity each
  $g --files $f --depth $f --assign rank                        # groups equal files
done
