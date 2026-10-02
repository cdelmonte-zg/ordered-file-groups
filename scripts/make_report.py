"""Write RESULTS.md, the report of round 5 (PLAN.md), from results/round-5/.

Run from the repository root:
  python scripts/make_report.py

Tables come from summary.tsv, plan-check.tsv and the manifests. The prose of
the observations and of the hypothesis verdicts is in this file and was
written after reading the tables; every figure in it is taken from them.
"""
import csv
import platform
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REF = ROOT / "results" / "round-5"
MANIFESTS = ROOT / "results" / "manifests"
PROVENANCE = ROOT / "results" / "round-4"   # binaries, patch, build logs of rounds 3 to 5

AXES = [
    ("base", "The base case", "12 files, depth 4, shape S1, 256 MB, Q3, four variants."),
    ("A1", "A1: the consumer of the order", "Base data and pool; the query changes."),
    ("A2", "A2: the overlap depth", "Base data at depth 1, 2 and 12 (depth 4 is the base case)."),
    ("A3", "A3: the memory budget", "Base data and query at 128 and 512 MB (256 MB is the base case)."),
    ("A4", "A4 and A4 x A3: the width of the rows", "Shapes S0 and S2 at the three pool sizes; S1 at the three pool sizes is the base case and A3."),
    ("A5", "A5 and A5 x A3: the number of files for the same rows", "120 and 1200 files, with depth 4 (one entity per file) and with depth equal to the files (total overlap); the 12-file points are the base case and A2 at depth 12."),
    ("A6", "A6: the share of duplicates", "Base data with half the rows copied. The base data itself has 24 duplicate rows out of 600,000 (share 0.00004), so the axis compares almost no duplicates with half."),
]

VARIANT_ORDER = ["original", "accept-groups", "original-target", "original-split-off"]


def read_tsv(path):
    with path.open() as f:
        return list(csv.DictReader(f, delimiter="\t"))


def rows_of(summary, axis):
    sel = [r for r in summary if r["axis"] == axis]
    if axis != "base":
        sel = [r for r in summary if r["case"] == "base" and r["variant"] != "original-split-off"] + sel
    return sorted(sel, key=lambda r: (r["case"] != "base", r["case"], int(r["pool"][:-1]),
                                       VARIANT_ORDER.index(r["variant"])))


def ok_cell(r):
    return "" if r["ok"].endswith("/10") and r["ok"].startswith("10/") else f" ({r['ok']})"


def elapsed(r):
    if r["ok"].startswith("0/"):
        return f"fails ({r['ok']})"
    return f"{r['elapsed_med']} [{r['elapsed_q1']}..{r['elapsed_q3']}]{ok_cell(r)}"


def spill(r, key):
    c, mb = r[f"{key}_spills"], r[f"{key}_spill_mb"]
    if c == "":
        return "-"
    return "0" if float(c) == 0 else f"{float(c):.0f} / {mb} MB"


def table(summary, axis, with_bytes=False):
    head = ["case", "pool", "variant", "groups", "sort", "modes", "elapsed s, median [q1..q3]",
            "RSS MB", "sort spills", "partial agg", "final agg", "repartition"]
    if with_bytes:
        head += ["scan out MB", "partial out MB", "repartition out MB", "final out MB"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows_of(summary, axis):
        modes = r["final_mode"] or "-"
        if r["partial_mode"] and r["partial_mode"] != r["final_mode"]:
            modes = f'{r["partial_mode"]} / {r["final_mode"]}'
        cells = [r["case"], r["pool"], r["variant"], r["groups"], r["sort"], modes, elapsed(r),
                 r["rss_mb_med"], spill(r, "sort"), spill(r, "partial_agg"), spill(r, "final_agg"),
                 spill(r, "repartition")]
        if with_bytes:
            cells += [r["scan_out_mb"], r["partial_agg_out_mb"], r["repartition_out_mb"],
                      r["final_agg_out_mb"]]
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    return "\n".join(lines)


def datasets():
    lines = ["| dataset | files | depth | assignment | shape | rows | distinct keys | duplicate rows | groups by bounds | bytes | row groups |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for path in sorted(MANIFESTS.glob("*.tsv")):
        text = path.read_text().splitlines()
        meta = {}
        for line in text:
            if not line.startswith("# "):
                break
            parts = line[2:].split("\t")
            meta[parts[0]] = parts[1]
        if "assign" not in meta:
            continue  # manifests of the earlier rounds
        body = [l.split("\t") for l in text if l and not l.startswith("#")][1:]
        size = sum(int(b[2]) for b in body)
        rgs = sum(int(b[3]) for b in body)
        lines.append(f'| {meta["dataset"]} | {meta["files"]} | {meta["depth"]} | {meta["assign"]} '
                     f'| {meta["shape"]} | {meta["rows"]} | {meta["distinct_grouping_keys"]} '
                     f'| {int(meta["rows"]) - int(meta["distinct_grouping_keys"]):,} | {meta["groups_by_bounds"]} | {size:,} | {rgs} |')
    return "\n".join(lines)


def plan_check():
    rows = read_tsv(REF / "plan-check.tsv")
    lines = ["| case | variant | query | groups | SortExec | preserve_order | partial mode | final mode | output_ordering |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f'| {r["case"]} | {r["variant"]} | {r["query"]} | {r["scan_groups"]} | {r["sort_exec"]} '
                     f'| {r["preserve_order"]} | {r["partial_mode"] or "-"} | {r["final_mode"] or "-"} | {r["output_ordering"]} |')
    return "\n".join(lines)


def main():
    summary = read_tsv(REF / "summary.tsv")
    cpu = next(l.split(":", 1)[1].strip() for l in
               subprocess.run(["lscpu"], capture_output=True, text=True).stdout.splitlines()
               if l.startswith("Model name"))
    threads = subprocess.run(["nproc"], capture_output=True, text=True).stdout.strip()
    hashes = (PROVENANCE / "binaries.sha256").read_text().strip()
    toolchain = (PROVENANCE / "toolchain.txt").read_text().splitlines()
    patch = (PROVENANCE / "accept-extra-groups.patch").read_text().strip()

    sections = []
    for axis, title, intro in AXES:
        sections.append(f"### {title}\n\n{intro}\n\n{table(summary, axis, with_bytes=(axis == 'A4'))}")
    axis_tables = "\n\n".join(sections)

    (ROOT / "RESULTS.md").write_text(f"""# Round 5: one base case, six axes, two crossings

Generated by `scripts/make_report.py` from `results/round-5/`. The design and the
hypotheses are in `PLAN.md`, written before the runs. Rounds 1 to 4, a single
scenario, are reported in `results/round-4/REPORT.md`.

## Provenance

- DataFusion commit e1aa7d956a5aa67452c9e8bd2a033599767055d8 (55.1.0, 2026-09-28),
  built in a clean detached worktree with {toolchain[0]}; {toolchain[-1]};
  `cargo build --release -p datafusion-cli`. `original` is the commit as it is,
  `accept-groups` the commit plus `patch/accept-extra-groups.patch`, the only change:

```diff
{patch}
```

- SHA-256 of the binaries (`results/round-4/binaries.sha256`, the same files as
  in rounds 3 and 4, verified with `sha256sum -c` before the run):

```
{hashes}
```

- {cpu}, {threads} threads, Linux {platform.release()}; `datafusion-cli
  --mem-pool-type fair --memory-limit <pool>`; `target_partitions = 2` except
  for `original-target`, where it equals the ordered groups the overlap needs;
  `split_file_groups_by_statistics = true` except for `original-split-off`.
- Ten recorded runs per case, pool and variant after one unrecorded warm-up,
  variants in rotating order. Times are the `Elapsed` of `EXPLAIN ANALYZE`, which
  starts before the logical plan is built and includes the file listing and the
  statistics reads of the scan; spills and `output_bytes` per operator from the
  plan metrics; RSS from `/usr/bin/time`. Medians with first and third quartile
  over completed runs; the count of completed runs is given when below ten.
- Every run's SQL, output and stderr: `results/round-5/<case>/<pool>/`.

## Datasets

All from `scripts/generate_round5.sh`, fixed seeds, fixed time origin; the same
600,000 rows redistributed, except the duplicate dataset, which holds the same
number of rows with half of them copies.

{datasets()}

## Plans, checked before timing

`EXPLAIN FORMAT INDENT` once per case and variant (`results/round-5/<case>/plan-check/`).

{plan_check()}

## Results by axis

Spill cells: count / spilled MB (medians over completed runs). Modes: of the
final aggregate, or partial / final when they differ. "Out MB" columns: the
cumulative `output_bytes` an operator reports in its metrics, the bytes of
the batches it emitted as Arrow accounts for their buffers; not peak resident
memory, and not necessarily unique bytes.

{axis_tables}

## Observations

{OBSERVATIONS}

## Hypotheses

{HYPOTHESES}

## Not measured

{NOT_MEASURED}
""")
    print(ROOT / "RESULTS.md")


OBSERVATIONS = """\
1. Base case, 256 MB, Q3. The original plan sorts and spills (sort 7 spills, 72.7
   MB; final aggregate 10 spills, 73.0 MB) in 0.647 s. `accept-groups` keeps the
   order with four groups, spills nowhere, and takes 0.376 s with 130 MB less RSS.
   `original-target`, the workaround, is faster still at 0.294 s, with four
   partitions above the scan, 9 small spills in the repartition and 90 MB more
   RSS than `accept-groups`. `original-split-off` equals the original: with the
   grouping by statistics off, the fallback is the plan a user gets anyway.
2. A1. Q1 (`ORDER BY` only): the order replaces a sort by a merge, 0.054 s against
   0.029 s, and halves the RSS (328 against 175 MB). Q2 (`GROUP BY` on the whole
   sort key): the ordered plan runs in `Sorted` mode and takes the same time as
   the hash plan, 0.061 against 0.063 s, with no spill in either and a higher RSS
   (294 against 228 MB); at this size the full streaming mode buys nothing
   measurable in time. Q3: 0.376 against 0.647 s, the base case. Q4 (`GROUP BY`
   without the sort key): the optimizer projects the ordering away and both
   binaries scan two byte-range groups, so the plans are identical and so are
   the times, 0.018 s; only `original-target`, with four groups, differs, by
   1 ms and 64 MB of RSS.
3. A2. At depth 1 and 2 the variants have the same plan and the same time. At
   depth 12 the gain of `accept-groups` is the same as at depth 4 (0.354 against
   0.622 s, a 43 percent reduction, against 42 percent at depth 4); what grows
   is the cost beside the time: 18 spills (18.9 MB) in the order-preserving
   repartition and 574 MB of RSS against 364. `original-target` with twelve
   partitions matches the time (0.362 s) at the price of 64 spills (124 MB) in
   the final aggregate, 140 (38.5 MB) in the repartition and 865 MB of RSS.
4. A3. With the base data the times do not move with the pool: the original
   0.685, 0.647, 0.655 s at 128, 256, 512 MB; `accept-groups` 0.367, 0.376,
   0.373 s. The original spills at every pool (more at 128 MB: 15 sort spills,
   22 in the final aggregate, 139 MB), the ordered plan never spills in the
   final aggregate and only lightly in the repartition at 128 MB (7 spills,
   4.2 MB). `original-target` loses its edge at 128 MB (0.382 s, with spills in
   the final aggregate).
5. A4 x A3. The width of the strings changes the output bytes the operators
   above the scan report: at 256 MB, the partial aggregate of the ordered plan
   reports 136 MB in S0, 248 MB in S1 and 453 MB in S2 for the same 600,000
   rows; the order-preserving repartition 64, 471 and 1157 MB; the scan 68 MB
   in every shape. Crossing the 12-byte limit on `col_3` and `col_4` (S2) is what brings
   spills to the ordered plan at 128 MB (final aggregate 14 spills, 92 MB) and
   shrinks its gain there to 11 percent (0.641 against 0.720 s), where S0 and S1
   gain 46 percent; at 256 and 512 MB S2 gains 39 and 41 percent, S0 and S1 41
   to 43. The original plan on S2 at 128 MB fails in 2 runs of
   10 with an allocation error in `SortPreservingMergeExec[0]`. Crossing the
   limit on `col_1` alone (S0 to S1) raises the reported bytes, adds small
   spills in the repartition at 128 MB (7 spills, 4.2 MB, against none in S0)
   and none in the final aggregate, and costs 0.02 s.
6. A5. With depth 4 and one entity per file, going from 12 to 120 to 1200 files
   costs both variants: the original 0.647, 0.679, 0.771 s, `accept-groups`
   0.376, 0.348, 0.475 s, that is +19 and +26 percent; the gap between them
   stays between 0.27 and 0.33 s. At 1200 files the statistics produce five
   groups, not four: a first-fit on the recorded bounds also gives five, so the
   fifth is in the bounds, not in the placement rule. Files 6 and 10 of the
   first entity touch, the maximum of one equal to the minimum of the other (a
   repeated timestamp on the boundary), and the placement requires a strictly
   greater minimum; 248 pairs of files touch in that layout. The
   `CREATE EXTERNAL TABLE` statement takes 0.002, 0.007 and 0.030 s. With the
   intended total overlap, 120 ordered groups win at 256 MB (0.410 against 0.665 s)
   with 125 spills (39 MB) in the repartition and 1035 MB of RSS against 657;
   1196 groups (of 1200 intended) lose at 128 MB (0.845 against 0.743 s, 26 spills and 94 MB in the
   final aggregate), win at 256 MB (0.656 against 0.751) and at 512 MB (0.649
   against 0.692), with about 2 GB of RSS at every pool against 0.55 to 0.65.
7. A6. With half the rows duplicated both plans get faster, the original from
   0.647 to 0.400 s, `accept-groups` from 0.376 to 0.196 s; the ratio between
   them moves from 0.58 to 0.49, and the original's spills halve in size.
8. The workaround (`original-target`) is the fastest variant in most cases
   (base, 512 MB, S0 at every pool, S2 at every pool, the duplicate dataset),
   at the price of more RSS; it keeps that edge on S2 at 128 MB even while
   spilling in the final aggregate (0.478 against 0.641 s). It loses it in two
   cases: the base data at 128 MB (0.382 against 0.367 s) and twelve groups
   (0.362 against 0.354 s), both with spills in the final aggregate.
9. Not explained, as in the earlier rounds: depth 1 is slower than depth 2 with
   the same plan (0.506 against 0.433 s)."""

HYPOTHESES = """\
- **H1, partly supported.** Q1 and Q3 as predicted. Q2 not: the `Sorted` mode
  and the hash plan take the same time at 256 MB with 600,000 rows, and the
  ordered plan uses more memory; the order buys the most for Q3, not for Q2. Q4:
  the plans of `original` and `accept-groups` are identical, so there is no
  scan effect to describe between them; the four-group scan of
  `original-target` costs 1 ms and 64 MB.
- **H2, half supported.** Depth 1 and 2 identical, as predicted. The gain does
  not shrink at depth 12 at 256 MB (43 percent against 42); the repartition's
  spills and the RSS grow, the time does not. The prediction was wrong about
  where the extra groups show up.
- **H3, not supported in its first part.** The gain does not grow with the pool:
  it is 46 percent at 128, 42 at 256, 43 at 512 MB, because with the base data
  the ordered plan never spills in the final aggregate at any pool and the
  original always does. The second part holds: at 128 MB the ordered plan wins
  on Q3.
- **H4, supported as an observed effect.** Crossing the limit on `col_3` and
  `col_4` raises the output bytes the operators report (the repartition's most,
  471 to 1157 MB at 256 MB), brings spills to the ordered plan at 128 MB and
  shrinks its gain there; at 512 MB the ranking is unchanged. S0 against S1
  shows the effect of `col_1` on the reported bytes, small repartition spills
  at 128 MB and almost no effect on time. What the reported bytes measure, and
  how a longer string produces them, is in "Not measured".
- **H5, partly supported.** Depth 4: the per-file costs are not small (+19
  percent for the original, +26 for `accept-groups` from 12 to 1200 files) and
  the groups are five, not four, so the control on the number of streams is
  imperfect; what holds is that the gap between the variants stays. Intended
  total overlap: 1196 groups lose at 128 MB and win at 256 and 512 MB, with
  about 2 GB of RSS throughout; the distinction between a few streams and
  about 1200 is the result that stands.
- **H6, measured.** The ordered plan profits more from the duplicates than the
  hash plan: its time falls by 48 percent, the original's by 38.
- **H7, supported.** `original-target` has the operator kinds of `accept-groups`
  with four partitions above the scan, a different time (faster at the base
  case, slower at 128 MB and at twelve groups) and more RSS everywhere; on Q1
  it equals `accept-groups`, on Q4 it is the only variant whose plan differs
  from the original."""

NOT_MEASURED = """\
- What the RSS of the many-group plans is made of (about 2 GB with 1196
  groups at every pool size).
- Which allocations push the final aggregate of the ordered plan into spilling
  at 128 MB with shape S2, and not with S0 or S1; the spill counts and bytes
  are measured, the cause is not attributed.
- What `output_bytes` measures beyond its definition (the cumulative bytes of
  the batches an operator emitted, as Arrow accounts for their buffers): it is
  not peak resident memory and not necessarily unique bytes, and in the
  original plan the partial aggregate of S0 reports 521, 975 and 424 MB at
  the three pools for the same rows. How a string above 12 bytes turns into
  the measured rise; the threshold coincides with the inline limit of Arrow
  string views, the mechanism was not traced in the code.
- The per-file overhead of small files is inside the elapsed time and the
  `CREATE` time, not broken down into opens, footer reads and metadata.
- Why depth 1 is slower than depth 2 with the same plan.
- Q2 at a size where the hash aggregate would spill; here it does not.
- The open-file limit was not repeated in round 5; the round-4 result stands
  (`results/round-4/fd-limit/`)."""

if __name__ == "__main__":
    main()
