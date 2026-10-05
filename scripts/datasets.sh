#!/usr/bin/env bash
# Every dataset of the lab, in /tmp/df-16919-*. Run from the repository root.
# The manifests go to results/manifests/.
set -euo pipefail
py=${PYTHON:-python}
g="$py scripts/generate_partial_overlap.py"
rm -rf /tmp/df-16919-base
# the matrix (DESIGN.md)
for d in 1 2 4 12; do $g --depth $d; done                     # A2, base at depth 4 (S1, entity)
$g --depth 4 --shape S0                                        # A4
$g --depth 4 --shape S2                                        # A4
$g --depth 4 --duplicate-share 0.5                             # A6
for f in 120 1200; do                                          # A5; the 12-file points are depth 4 and depth 12 above
  $g --files $f --depth 4  --assign entity-rank                 # groups stay 4, files hold one entity each
  $g --files $f --depth $f --assign rank                        # groups equal files
done
# the experiments
for f in 150 300 600; do $g --files $f --depth $f --assign rank; done      # many-streams
for d in 1 2; do $g --files 120 --depth $d --assign entity-rank; done       # depth
# rows: more grouping keys under each prefix (the prefixes stay) ...
$g --depth 4 --rows 6000000  --name df-16919-partial-12-depth-4-6m
$g --depth 4 --rows 24000000 --name df-16919-partial-12-depth-4-24m
# ... and more prefixes with the keys under each as in the base case (cluster width x10, x40;
# the prefixes each dataset really has are in its manifest)
$g --depth 4 --rows 6000000  --cluster-ms 5000  --name df-16919-partial-12-depth-4-6m-prefixes
$g --depth 4 --rows 24000000 --cluster-ms 20000 --name df-16919-partial-12-depth-4-24m-prefixes
# ... and the keys concentrated under few prefixes (cluster width 1 ms)
$g --depth 4 --rows 6000000  --cluster-ms 1     --name df-16919-partial-12-depth-4-6m-concentrated
