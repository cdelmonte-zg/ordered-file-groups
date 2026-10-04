#!/usr/bin/env bash
# Put the machine in the state the lab measures in, and put it back afterwards.
#
#   scripts/machine_setup.sh status     what the settings are now (no root needed)
#   scripts/machine_setup.sh check      exit 0 when the machine is configured for the lab
#   sudo scripts/machine_setup.sh apply     configure; the previous values are saved
#   sudo scripts/machine_setup.sh restore   put the saved values back
#
# What `apply` sets, and why:
#
#   CPU frequency governor = performance, on every CPU, and the energy
#     preference = performance where the driver has one (amd-pstate-epp,
#     intel_pstate): with powersave the frequency follows the load, and a query
#     that lasts half a second is measured while the cores are still ramping up.
#   power profile = performance, when power-profiles-daemon runs: it would
#     otherwise put the governor back.
#   swap off: the peak RSS is a measured quantity and must not be pages that
#     the kernel moved out.
#
# Pinning needs no root and is not done here: the runners pin themselves, and
#   every process they start, to the CPUs that share the largest last-level
#   cache (LAB_CPUS=<list> to choose them, LAB_CPUS=all for no pinning).
#
# What it does not touch, on purpose, and only reports: frequency boost (turbo),
#   SMT, transparent huge pages, address-space randomization, idle states.
#   Changing them changes what is measured rather than how steadily; set
#   BOOST=0 to switch the boost off as well, for less variance at lower speed.
#
# Nothing is persistent: a reboot undoes it. The values in force when the
# matrix starts are recorded in results/matrix/machine.txt and in the report.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
state=$root/.machine-state          # saved by apply, read by restore; not tracked
cpus=/sys/devices/system/cpu
paranoid=/proc/sys/kernel/perf_event_paranoid
boost=$cpus/cpufreq/boost

first() { cat "$1" 2>/dev/null || echo "n/a"; }
all_same() {                         # the value of a per-CPU file when every CPU agrees
  local values
  values=$(cat $cpus/cpu[0-9]*/cpufreq/"$1" 2>/dev/null | sort -u | tr '\n' ' ' | sed 's/ $//')
  echo "${values:-n/a}"
}
thp() { sed -n 's/.*\[\(.*\)\].*/\1/p' /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || echo "n/a"; }
swap_in_use() { if [ -n "$(swapon --noheadings --show=NAME 2>/dev/null)" ]; then echo on; else echo off; fi; }
profile() { if command -v powerprofilesctl >/dev/null 2>&1; then powerprofilesctl get 2>/dev/null || echo "n/a"; else echo "n/a"; fi; }

status() {
  printf 'scaling_driver\t%s\n' "$(first $cpus/cpu0/cpufreq/scaling_driver)"
  printf 'governor\t%s\n' "$(all_same scaling_governor)"
  printf 'energy_performance_preference\t%s\n' "$(all_same energy_performance_preference)"
  printf 'power_profile\t%s\n' "$(profile)"
  printf 'boost\t%s\n' "$(first $boost)"
  printf 'smt\t%s\n' "$(first $cpus/smt/control)"
  printf 'transparent_hugepage\t%s\n' "$(thp)"
  printf 'swap\t%s\n' "$(swap_in_use)"
  printf 'randomize_va_space\t%s\n' "$(first /proc/sys/kernel/randomize_va_space)"
}

check() {
  local bad=0
  [ "$(all_same scaling_governor)" = performance ] || { echo "governor is not performance on every CPU" >&2; bad=1; }
  [ "$(swap_in_use)" = off ] || { echo "swap is on" >&2; bad=1; }
  return $bad
}

need_root() { [ "$(id -u)" = 0 ] || { echo "run with sudo: sudo $0 $1" >&2; exit 2; }; }
write_all() {                        # write a value to a per-CPU cpufreq file, where it exists
  local f
  for f in $cpus/cpu[0-9]*/cpufreq/"$1"; do [ -w "$f" ] && echo "$2" > "$f" || true; done
}

case ${1:-} in
  status) status ;;
  check) check ;;
  apply)
    need_root apply
    if [ -e "$state" ]; then
      echo "$state exists: the machine is already configured, or run restore first" >&2; exit 2
    fi
    {
      for f in $cpus/cpu[0-9]*/cpufreq; do      # per CPU: they need not be alike
        cpu=$(basename "$(dirname "$f")")
        printf 'governor:%s\t%s\n' "$cpu" "$(first "$f/scaling_governor")"
        printf 'energy_performance_preference:%s\t%s\n' "$cpu" "$(first "$f/energy_performance_preference")"
      done
      printf 'power_profile\t%s\n' "$(profile)"
      printf 'boost\t%s\n' "$(first $boost)"
      printf 'swap\t%s\n' "$(swapon --noheadings --show=NAME 2>/dev/null | tr '\n' ' ')"
    } > "$state"
    [ -n "${SUDO_UID:-}" ] && chown "$SUDO_UID:${SUDO_GID:-$SUDO_UID}" "$state"
    if [ "$(profile)" != "n/a" ]; then powerprofilesctl set performance || true; fi
    write_all scaling_governor performance
    write_all energy_performance_preference performance
    if [ "${BOOST:-}" = 0 ] && [ -w "$boost" ]; then echo 0 > "$boost"; fi
    swapoff -a || echo "swapoff failed: swap stays on; restore still puts the rest back" >&2
    status
    check
    ;;
  restore)
    need_root restore
    [ -e "$state" ] || { echo "no saved state in $state" >&2; exit 2; }
    saved() { awk -F'\t' -v k="$1" '$1 == k {print $2}' "$state"; }
    put() {                              # the value saved for one CPU, or the one saved for all
      local file=$1 key=$2 cpu=$3 value
      value=$(saved "$key:$cpu"); [ -n "$value" ] || value=$(saved "$key")
      [ -n "$value" ] && [ "$value" != "n/a" ] && [ -w "$file" ] && echo "$value" > "$file" || true
    }
    for f in $cpus/cpu[0-9]*/cpufreq; do
      cpu=$(basename "$(dirname "$f")")
      put "$f/scaling_governor" governor "$cpu"
      put "$f/energy_performance_preference" energy_performance_preference "$cpu"
    done
    [ "$(saved power_profile)" = "n/a" ] || powerprofilesctl set "$(saved power_profile)" || true
    if [ "$(saved boost)" != "n/a" ] && [ -w "$boost" ]; then echo "$(saved boost)" > "$boost"; fi
    # a state saved by an earlier version of this script may hold it
    [ -z "$(saved perf_event_paranoid)" ] || echo "$(saved perf_event_paranoid)" > "$paranoid"
    for device in $(saved swap); do swapon "$device" || true; done
    rm -f "$state"
    status
    ;;
  *) sed -n '2,8p' "$0" >&2; exit 2 ;;
esac
