#!/usr/bin/env bash
# Run the 1000-file query under an open-file limit with both binaries.
#
# Usage: scripts/fd_limit_test.sh <results-dir> [limits...]   (default limits: 1024 4096)
# Needs bin/ with the two binaries, the dataset /tmp/df-16919-overlap-1000-x-600
# and the SQL written by run_bench.py in <results-dir>/mem-128m/.
set -uo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
results=${1:?results directory of the round}
shift
limits=${*:-1024 4096}
sql=$results/mem-128m/overlap-1000-x-600.sql
out=$results/fd-limit
mkdir -p "$out"
for n in $limits; do
  for variant in original accept-groups; do
    ( ulimit -n "$n"
      /usr/bin/time -f "BENCH_WALL_SECONDS=%e BENCH_MAX_RSS_KB=%M" \
        "$root/bin/datafusion-cli-$variant-release" --memory-limit 128m --mem-pool-type fair \
        -f "$sql" > "$out/nofile-$n-$variant.out" 2> "$out/nofile-$n-$variant.err" )
    printf '%s %s: exit %s; %s\n' "$n" "$variant" "$?" "$(grep -v '^BENCH_' "$out/nofile-$n-$variant.err" | head -1)"
  done
done
