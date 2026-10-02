#!/usr/bin/env bash
# Run from the root of the DataFusion checkout.
# Requires both release binaries and the two SQL files in scratch-16919/.
set -euo pipefail

if [[ ! -f Cargo.toml || ! -d scratch-16919 ]]; then
  printf 'Run this script from the root of the DataFusion checkout.\n' >&2
  exit 1
fi

work_dir='scratch-16919'
result_dir="$work_dir/bench-release"
mkdir -p "$result_dir"

for variant in original accept-groups; do
  bin="$work_dir/datafusion-cli-${variant}-release"
  if [[ ! -x "$bin" ]]; then
    printf 'Missing executable: %s\n' "$bin" >&2
    exit 1
  fi
done

for dataset in repro_medium repro_overlap_12_target_2; do
  sql="$work_dir/${dataset}.sql"
  if [[ ! -f "$sql" ]] || ! grep -Eiq 'EXPLAIN[[:space:]]+ANALYZE' "$sql"; then
    printf 'Expected an EXPLAIN ANALYZE SQL file: %s\n' "$sql" >&2
    exit 1
  fi
done

summary="$result_dir/results.tsv"
printf 'dataset\tvariant\trun\telapsed_seconds\tfile_groups\n' > "$summary"

for dataset in repro_medium repro_overlap_12_target_2; do
  for run in 1 2 3; do
    if (( run % 2 == 0 )); then
      variants=(accept-groups original)
    else
      variants=(original accept-groups)
    fi

    for variant in "${variants[@]}"; do
      bin="$work_dir/datafusion-cli-${variant}-release"
      sql="$work_dir/${dataset}.sql"
      out="$result_dir/${dataset}-${variant}-${run}.out"
      err="$result_dir/${dataset}-${variant}-${run}.err"

      printf 'Running %s, %s, repetition %s...\n' "$dataset" "$variant" "$run"
      if "$bin" --memory-limit 128m --mem-pool-type fair -f "$sql" \
          > "$out" 2> "$err"; then
        :
      else
        status=$?
        printf 'CLI failed (status %s): %s\n' "$status" "$err" >&2
        tail -n 30 "$err" >&2
        exit "$status"
      fi

      # Some CLI errors are reported as query errors even if the process exits 0.
      if ! grep -Fq 'Plan with Metrics' "$out" \
          || grep -Eq 'Resources exhausted|Error:' "$err"; then
        printf 'Query did not complete successfully: %s\n' "$out" >&2
        tail -n 30 "$err" >&2
        exit 1
      fi

      expected_groups=2
      if [[ "$variant" == accept-groups ]]; then
        if [[ "$dataset" == repro_medium ]]; then
          expected_groups=3
        else
          expected_groups=12
        fi
      fi
      if ! grep -Fq "file_groups={$expected_groups groups:" "$out"; then
        printf 'Unexpected file group count in %s (expected %s).\n' \
          "$out" "$expected_groups" >&2
        exit 1
      fi

      if [[ "$variant" == original ]]; then
        if ! grep -Fq 'SortExec:' "$out"; then
          printf 'Expected SortExec in %s.\n' "$out" >&2
          exit 1
        fi
      elif grep -Fq 'SortExec:' "$out"; then
        printf 'Unexpected SortExec in %s.\n' "$out" >&2
        exit 1
      fi

      elapsed=$(awk '/^Elapsed [0-9]+([.][0-9]+)? seconds[.]$/ {last=$2}
                     END {print last}' "$out")
      if [[ -z "$elapsed" ]]; then
        printf 'Could not extract elapsed time from %s.\n' "$out" >&2
        exit 1
      fi
      printf '%s\t%s\t%s\t%s\t%s\n' \
        "$dataset" "$variant" "$run" "$elapsed" "$expected_groups" >> "$summary"
      printf '  %.3f s; %s file groups\n' "$elapsed" "$expected_groups"
    done
  done
done

printf '\nMedian elapsed times (three release runs, 128m, fair):\n'
for dataset in repro_medium repro_overlap_12_target_2; do
  for variant in original accept-groups; do
    median=$(awk -F '\t' -v d="$dataset" -v v="$variant" \
      '$1 == d && $2 == v {print $4}' "$summary" | sort -n | sed -n '2p')
    printf '  %-27s %-14s %s s\n' "$dataset" "$variant" "$median"
  done
done
printf '\nFull plans, errors and results: %s/\n' "$result_dir"
