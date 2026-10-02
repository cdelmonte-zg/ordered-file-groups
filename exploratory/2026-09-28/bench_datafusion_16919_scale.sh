#!/usr/bin/env bash
# Run from the DataFusion repository root.
# Usage: bash scratch-16919/bench_datafusion_16919_scale.sh [files] [rows_per_file]
# Optional: RUNS=3 TIMEOUT_SECONDS=180 PYTHON=python GENERATOR=/path/to/generator.py
set -euo pipefail

files=${1:-1000}
rows_per_file=${2:-600}
runs=${RUNS:-3}
timeout_seconds=${TIMEOUT_SECONDS:-180}
python_bin=${PYTHON:-python}
generator=${GENERATOR:-}
if [[ -z "$generator" ]]; then
  for candidate in scratch-16919/generate_16919_sorted.py generate_16919_sorted.py; do
    if [[ -f "$candidate" ]]; then
      generator=$candidate
      break
    fi
  done
fi
if [[ -z "$generator" ]]; then
  printf 'Could not locate generate_16919_sorted.py; set GENERATOR=/path/to/script.py.\n' >&2
  exit 1
fi

for value in "$files" "$rows_per_file" "$runs" "$timeout_seconds"; do
  if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
    printf 'Expected positive integer, got: %s\n' "$value" >&2
    exit 1
  fi
done

work_dir=scratch-16919
template="$work_dir/repro_overlap_12_target_2.sql"
original="$work_dir/datafusion-cli-original-release"
accept="$work_dir/datafusion-cli-accept-groups-release"
dataset="overlap-${files}-x-${rows_per_file}"
data_dir="/tmp/df-16919-$dataset"
result_dir="$work_dir/bench-scale/$dataset"

for path in "$generator" "$template" "$original" "$accept"; do
  if [[ ! -e "$path" ]]; then
    printf 'Missing: %s (run from repository root)\n' "$path" >&2
    exit 1
  fi
done
for bin in "$original" "$accept"; do
  if [[ ! -x "$bin" ]]; then
    printf 'Not executable: %s\n' "$bin" >&2
    exit 1
  fi
done
if [[ ! -x /usr/bin/time ]] || ! command -v timeout >/dev/null; then
  printf 'Requires /usr/bin/time and GNU timeout.\n' >&2
  exit 1
fi

mkdir -p "$result_dir"
if [[ ! -e "$data_dir" ]]; then
  "$python_bin" "$generator" --rows "$rows_per_file" --files "$files" \
    --output-dir "$data_dir"
else
  printf 'Reusing existing dataset: %s\n' "$data_dir"
fi
if [[ ! -d "$data_dir" ]]; then
  printf 'Generator finished but did not create %s. Check the generator output.\n' \
    "$data_dir" >&2
  exit 1
fi
actual_files=$(find "$data_dir" -maxdepth 1 -type f -name 'reproducible_data_*.parquet' | wc -l)
if (( actual_files != files )); then
  printf 'Expected %s Parquet files in %s; found %s. Check this directory.\n' \
    "$files" "$data_dir" "$actual_files" >&2
  exit 1
fi

"$python_bin" - "$template" "$result_dir" "$data_dir" <<'PY'
from pathlib import Path
import re
import sys

template, result_dir, data_dir = map(Path, sys.argv[1:])
sql = template.read_text()
sql, locations = re.subn(r"\bLOCATION\s*'[^']*'", f"LOCATION '{data_dir}/'", sql, count=1, flags=re.I)
if locations != 1 or not re.search(r"\bEXPLAIN\s+ANALYZE\b", sql, re.I):
    raise SystemExit("Expected one LOCATION and EXPLAIN ANALYZE in template SQL")
if not re.search(r"\btarget_partitions\s*=\s*2\s*;", sql, re.I):
    raise SystemExit("Template SQL must set target_partitions = 2")
if not re.search(r"\bsplit_file_groups_by_statistics\s*=\s*true\s*;", sql, re.I):
    raise SystemExit("Template SQL must enable split_file_groups_by_statistics")
(result_dir / "query.sql").write_text(sql)
plan = re.sub(r"\bEXPLAIN\s+ANALYZE\b", "EXPLAIN FORMAT INDENT", sql, count=1, flags=re.I)
(result_dir / "plan.sql").write_text(plan)
PY

# Verify that both binaries execute the expected different plans before timing.
for variant in original accept-groups; do
  bin="$work_dir/datafusion-cli-${variant}-release"
  plan_out="$result_dir/${variant}-plan.out"
  plan_err="$result_dir/${variant}-plan.err"
  if timeout "${timeout_seconds}s" "$bin" -f "$result_dir/plan.sql" \
      > "$plan_out" 2> "$plan_err"; then
    :
  else
    printf 'Planning failed for %s; see %s\n' "$variant" "$plan_err" >&2
    exit 1
  fi
done

"$python_bin" - "$result_dir" "$files" <<'PY'
from pathlib import Path
import re
import sys

directory = Path(sys.argv[1])
files = int(sys.argv[2])
for variant in ("original", "accept-groups"):
    text = (directory / f"{variant}-plan.out").read_text()
    match = re.search(r"DataSourceExec: file_groups=\{(\d+) groups?:", text)
    if not match:
        raise SystemExit(f"No file group count in {variant} plan; inspect saved output")
    groups = int(match.group(1))
    has_sort = "SortExec:" in text
    print(f"{variant}: scan_groups={groups}, SortExec={has_sort}")
    if variant == "original" and (groups != min(files, 2) or not has_sort):
        raise SystemExit("Original binary or SQL is not the expected baseline")
    if variant == "accept-groups" and (groups <= 2 or has_sort):
        raise SystemExit("Modified binary did not preserve the expected ordering")
PY

manifest="$result_dir/manifest.tsv"
printf 'variant\trun\texit_code\tresult\n' > "$manifest"
declare -A stopped=()

for (( run=1; run<=runs; run++ )); do
  if (( run % 2 == 0 )); then
    variants=(accept-groups original)
  else
    variants=(original accept-groups)
  fi
  for variant in "${variants[@]}"; do
    if [[ ${stopped[$variant]:-0} == 1 ]]; then
      continue
    fi
    bin="$work_dir/datafusion-cli-${variant}-release"
    out="$result_dir/${variant}-${run}.out"
    err="$result_dir/${variant}-${run}.err"
    printf 'Running %s: %s, repetition %s/%s...\n' "$dataset" "$variant" "$run" "$runs"
    status=0
    timeout "${timeout_seconds}s" /usr/bin/time \
      -f 'BENCH_WALL_SECONDS=%e BENCH_MAX_RSS_KB=%M' \
      "$bin" --memory-limit 128m --mem-pool-type fair \
      -f "$result_dir/query.sql" > "$out" 2> "$err" || status=$?
    result=ok
    if (( status != 0 )) || ! grep -Fq 'Plan with Metrics' "$out" \
        || grep -Eq 'Resources exhausted|\*\*Error\*\*|^Error:' "$err"; then
      result=failed
      stopped[$variant]=1
      printf '  Failed (exit %s); see %s and %s. Skipping later %s runs.\n' \
        "$status" "$out" "$err" "$variant" >&2
    fi
    printf '%s\t%s\t%s\t%s\n' "$variant" "$run" "$status" "$result" >> "$manifest"
  done
done

"$python_bin" - "$result_dir" <<'PY'
from pathlib import Path
import csv
import re
import statistics
import sys

directory = Path(sys.argv[1])
groups = {}
for variant in ("original", "accept-groups"):
    text = (directory / f"{variant}-plan.out").read_text()
    groups[variant] = int(re.search(r"DataSourceExec: file_groups=\{(\d+) groups?:", text).group(1))

rows = []
with (directory / "manifest.tsv").open() as f:
    for item in csv.DictReader(f, delimiter="\t"):
        variant, run = item["variant"], item["run"]
        out = (directory / f"{variant}-{run}.out").read_text()
        err = (directory / f"{variant}-{run}.err").read_text()
        elapsed = re.findall(r"^Elapsed ([\d.]+) seconds\.$", out, re.M)
        wall = re.search(r"BENCH_WALL_SECONDS=([\d.]+)", err)
        rss = re.search(r"BENCH_MAX_RSS_KB=(\d+)", err)
        spills = {"sort": 0, "final_aggregate": 0, "repartition": 0}
        for line in out.splitlines():
            match = re.search(r"\bspill_count=(\d+)", line)
            if match:
                if "SortExec:" in line:
                    spills["sort"] += int(match.group(1))
                elif "AggregateExec: mode=Final" in line:
                    spills["final_aggregate"] += int(match.group(1))
                elif "RepartitionExec:" in line:
                    spills["repartition"] += int(match.group(1))
        rows.append({
            **item, "scan_groups": groups[variant],
            "elapsed_seconds": elapsed[-1] if elapsed else "",
            "wall_seconds": wall.group(1) if wall else "",
            "max_rss_kb": rss.group(1) if rss else "",
            "sort_spills": spills["sort"],
            "final_aggregate_spills": spills["final_aggregate"],
            "repartition_spills": spills["repartition"],
        })

columns = ["variant", "run", "exit_code", "result", "scan_groups",
           "elapsed_seconds", "wall_seconds", "max_rss_kb", "sort_spills",
           "final_aggregate_spills", "repartition_spills"]
with (directory / "results.tsv").open("w") as f:
    writer = csv.DictWriter(f, fieldnames=columns, delimiter="\t")
    writer.writeheader()
    writer.writerows(rows)
print(f"\nResults: {directory / 'results.tsv'}")
for variant in ("original", "accept-groups"):
    measured = [float(r["elapsed_seconds"]) for r in rows
                if r["variant"] == variant and r["result"] == "ok" and r["elapsed_seconds"]]
    median = f"{statistics.median(measured):.3f} s" if measured else "no successful runs"
    print(f"  {variant}: {median} ({len(measured)} successful runs, {groups[variant]} scan groups)")
PY
