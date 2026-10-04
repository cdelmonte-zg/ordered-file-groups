"""The sections of RESULTS.md on the experiments, computed from their results.tsv.

Every number and every qualitative word in these sections comes from the
recorded runs; nothing is written by hand. Called by scripts/make_report.py.
"""
import ast
import random
import statistics as st
from math import sqrt, isnan
from pathlib import Path

from common import read_tsv

# t quantiles 0.975 by degrees of freedom; beyond 30 the normal value
T975 = dict(zip(range(1, 31), (
    12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
    2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
    2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042)))


def completed(rows):
    return [r for r in rows if str(r["ok"]) == "1"]


def values(rows, key):
    return [float(r[key]) for r in rows if r[key] != ""]


def median(rows, key):
    v = values(rows, key)
    return st.median(v) if v else float("nan")


def mean(rows, key):
    v = values(rows, key)
    return st.mean(v) if v else float("nan")


def stdev(rows, key):
    v = values(rows, key)
    return st.stdev(v) if len(v) > 1 else float("nan")


def num(x, digits=3):
    return "n/a" if x is None or isnan(x) else f"{x:.{digits}f}"


def span(lo, hi, digits=2):
    """'a to b', or 'a' when the two agree at the given precision."""
    a, b = num(lo, digits), num(hi, digits)
    return a if a == b else f"{a} to {b}"


def n_of(rows):
    return f"{len(completed(rows))} of {len(rows)}"


def select(rows, **where):
    return [r for r in rows if all(str(r[k]) == str(v) for k, v in where.items())]


def missing(name, out):
    return f"### {name}\n\nNot run: `{out}` has no results.tsv.\n"


# ---------------------------------------------------------------- string views

def string_views(out, figures):
    path = Path(out) / "results.tsv"
    if not path.is_file():
        return missing("String views and the repartition's accounting", out)
    rows = read_tsv(path)

    def cell(shape, pool, variant, strings):
        return select(rows, shape=shape, pool=pool, variant=variant, strings=strings)

    def shown(sel):
        ok = completed(sel)
        note = "" if len(ok) == len(sel) else f", {n_of(sel)} completed"
        return f"{num(median(ok, 'elapsed_s'))} ({num(median(ok, 'final_agg_spills'), 0)}{note})"

    def gain(shape, pool, strings):
        o = median(completed(cell(shape, pool, "original", strings)), "elapsed_s")
        a = median(completed(cell(shape, pool, "accept-groups", strings)), "elapsed_s")
        return 100 * (1 - a / o)

    lines = ["### String views and the repartition's accounting", "",
             "`experiments/string-views/run.py`: the deduplication on the narrow strings (S0) and on "
             "the wide ones (S2), read as Arrow string views and as plain `Utf8`, both plans, five "
             "runs each. Median elapsed seconds, with the median spills of the final aggregate in "
             "parentheses.", "",
             "| shape | pool | original, views | original, Utf8 | ordered, views | ordered, Utf8 |",
             "|---|---|---|---|---|---|"]
    cells = [(s, p) for s in ("S0", "S2") for p in ("128m", "512m")]
    for shape, pool in cells:
        lines.append(f"| {shape} | {pool} | " + " | ".join(
            shown(cell(shape, pool, v, s)) for v in ("original", "accept-groups")
            for s in ("views", "utf8")) + " |")
    lines += ["", "Gain of the ordered plan over the original:", "",
              "| shape | pool | with string views | with plain Utf8 |", "|---|---|---|---|"]
    for shape, pool in cells:
        lines.append(f"| {shape} | {pool} | {gain(shape, pool, 'views'):.0f} % | "
                     f"{gain(shape, pool, 'utf8'):.0f} % |")

    def med(variant, strings, key):
        return median(completed(cell("S2", "128m", variant, strings)), key)

    gv, gu = gain("S2", "128m", "views"), gain("S2", "128m", "utf8")
    narrow = gain("S0", "128m", "views")
    failed = len(cell("S2", "128m", "original", "views")) - len(completed(cell("S2", "128m", "original", "views")))
    lines += ["", f"- On the wide strings at 128 MB the ordered plan gains {gv:.0f} percent with "
              f"string views and {gu:.0f} percent with plain `Utf8`; on the narrow strings, with "
              f"views, {narrow:.0f} percent.",
              f"- The final aggregate of the ordered plan spills {num(med('accept-groups', 'views', 'final_agg_spills'), 0)} "
              f"times with views and {num(med('accept-groups', 'utf8', 'final_agg_spills'), 0)} with plain strings.",
              f"- The order-preserving repartition of the ordered plan reports "
              f"{num(med('accept-groups', 'views', 'repartition_out_mb'), 0)} MB of output with views and "
              f"{num(med('accept-groups', 'utf8', 'repartition_out_mb'), 0)} with plain strings; the original "
              f"plan's repartition {num(med('original', 'views', 'repartition_out_mb'), 0)} and "
              f"{num(med('original', 'utf8', 'repartition_out_mb'), 0)}.",
              f"- The views cost the original plan too: its sort spills "
              f"{num(med('original', 'views', 'sort_spills'), 0)} times with views and "
              f"{num(med('original', 'utf8', 'sort_spills'), 0)} with plain strings, its median is "
              f"{num(med('original', 'views', 'elapsed_s'))} against {num(med('original', 'utf8', 'elapsed_s'))} s"
              + (f", and {failed} of its five runs with views failed." if failed else "."), ""]
    figures += [("string_test_gain_views_S2_128_pct", f"{gv:.0f}"),
                ("string_test_gain_utf8_S2_128_pct", f"{gu:.0f}"),
                ("string_test_gain_views_S0_128_pct", f"{narrow:.0f}"),
                ("string_test_ordered_utf8_s", num(med('accept-groups', 'utf8', 'elapsed_s'))),
                ("string_test_original_utf8_s", num(med('original', 'utf8', 'elapsed_s'))),
                ("string_test_ordered_views_s", num(med('accept-groups', 'views', 'elapsed_s'))),
                ("string_test_original_views_s", num(med('original', 'views', 'elapsed_s'))),
                ("string_test_repartition_out_views_mb", num(med('accept-groups', 'views', 'repartition_out_mb'), 0)),
                ("string_test_repartition_out_utf8_mb", num(med('accept-groups', 'utf8', 'repartition_out_mb'), 0))]
    return "\n".join(lines)


# ---------------------------------------------------------------- many streams

def slope(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else None


def ols(xs, ys):
    """(intercept, slope, half width of the 95 % interval of the slope, R squared)."""
    n = len(xs)
    b = slope(xs, ys)
    mx, my = sum(xs) / n, sum(ys) / n
    a = my - b * mx
    res = [y - a - b * x for x, y in zip(xs, ys)]
    sxx = sum((x - mx) ** 2 for x in xs)
    se = sqrt(sum(e * e for e in res) / (n - 2)) / sqrt(sxx)
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = 1 - sum(e * e for e in res) / ss_tot if ss_tot else float("nan")
    return a, b, se * T975.get(n - 2, 1.960), r2


def many_streams(out, figures):
    path = Path(out) / "results.tsv"
    if not path.is_file():
        return missing("The memory of the many-stream plan", out)
    rows = completed(read_tsv(path))
    all_rows = read_tsv(path)
    series = sorted({r["series"] for r in all_rows}, key=int)
    failed = len(all_rows) - len(rows)
    files_list = ("150", "300", "600", "1200")

    def e1(s, q, files, variant):
        return select(rows, series=s, experiment="E1", query=q, files=files, variant=variant)

    def fit(s, q):
        """OLS and bootstrap of the excess RSS of the ordered plan on the ordered groups."""
        cells, xs, ys = [], [], []
        for f in files_list:
            o = [(int(r["groups"]), float(r["rss_mb"])) for r in e1(s, q, f, "accept-groups")]
            b = values(e1(s, q, f, "original"), "rss_mb")
            if o and b:
                cells.append((o, b))
                base = st.median(b)
                xs += [g for g, _ in o]
                ys += [v - base for _, v in o]
        if len(cells) < 3:
            return None
        a, b, ci, r2 = ols(xs, ys)
        rng = random.Random(16919 + int(s))
        boot = []
        for _ in range(4000):
            bx, by = [], []
            for o, base in cells:
                m = st.median(rng.choices(base, k=len(base)))
                for g, v in rng.choices(o, k=len(o)):
                    bx.append(g)
                    by.append(v - m)
            boot.append(slope(bx, by))
        boot.sort()
        return {"slope_kb": b * 1024, "lo": (b - ci) * 1024, "hi": (b + ci) * 1024, "r2": r2,
                "boot_lo": boot[int(0.025 * len(boot))] * 1024,
                "boot_hi": boot[int(0.975 * len(boot)) - 1] * 1024, "n": len(xs)}

    lines = ["### The memory of the many-stream plan", "",
             "`experiments/many-streams/run.py`: 150, 300, 600 and 1200 files with total overlap, "
             "256 MB. The peak RSS of this case moves from run to run, so the experiments are "
             f"repeated in {len(series)} independent series and every coefficient is given per "
             "series. " + (f"{failed} run(s) failed and are left out." if failed else "No run failed."), "",
             "**E1, excess RSS of the ordered plan over the original, regressed on the ordered "
             "groups.** Slope in KB per stream, with the 95 percent interval of the regression "
             "(which takes the baseline, the median of three original runs, as exact) and that "
             "of a bootstrap over the ordered and the original runs.", "",
             "| series | query | slope | 95 % interval | bootstrap interval | R² |", "|---|---|---|---|---|---|"]
    fits = {}
    for s in series:
        for q, label in (("Q1", "Q1, `ORDER BY` only"), ("Q2", "Q2, `GROUP BY` on the sort key, 3 columns read"),
                         ("Q3", "Q3, deduplication")):
            f = fits[s, q] = fit(s, q)
            if f is None:
                lines.append(f"| {s} | {label} | too few completed runs | | | |")
            else:
                lines.append(f"| {s} | {label} | {f['slope_kb']:.0f} | {f['lo']:.0f} to {f['hi']:.0f} | "
                             f"{f['boot_lo']:.0f} to {f['boot_hi']:.0f} | {f['r2']:.2f} |")

    def med_rss(sel):
        return median(sel, "rss_mb")

    lines += ["", "**E2, the deduplication at 1200 files by output partitions; E3, eager purging "
              "(`MIMALLOC_PURGE_DELAY=0`); P, one-variable probes.** Median peak RSS in MB, with "
              "the range of the runs.", "",
              "| series | 2 outputs | 4 outputs | 8 outputs | 2 outputs, purge 0 | plain Utf8 | batch 1024 | "
              "`ORDER BY`, ordered | `ORDER BY`, purge 0 | `ORDER BY`, original |", "|" + "---|" * 10]

    def rng_cell(sel):
        v = values(sel, "rss_mb")
        return f"{st.median(v):.0f} ({min(v):.0f} to {max(v):.0f})" if v else "n/a"

    per_series = {}
    for s in series:
        base = e1(s, "Q3", "1200", "accept-groups")
        t4 = select(rows, series=s, name="E2-1200-Q3-t4")
        t8 = select(rows, series=s, name="E2-1200-Q3-t8")
        purge3 = select(rows, series=s, name="E3-1200-Q3-purge0")
        purge1 = select(rows, series=s, name="E3-1200-Q1-purge0")
        q1o = e1(s, "Q1", "1200", "accept-groups")
        q1b = e1(s, "Q1", "1200", "original")
        lines.append(f"| {s} | " + " | ".join(rng_cell(x) for x in (
            base, t4, t8, purge3, select(rows, series=s, experiment="P-utf8"),
            select(rows, series=s, experiment="P-batch1024"), q1o, purge1, q1b)) + " |")
        groups = int(base[0]["groups"]) if base else 0
        original = med_rss(e1(s, "Q3", "1200", "original"))
        pair = (med_rss(t8) - med_rss(base)) / (6 * groups) if groups else float("nan")
        f1 = fits[s, "Q1"]
        scan = f1["slope_kb"] / 1024 * groups if f1 else float("nan")
        per_series[s] = {
            "groups": groups, "excess": med_rss(base) - original, "pair_mb": pair,
            "added_2_to_8": med_rss(t8) - med_rss(base),
            "predicted_4": med_rss(base) + pair * 2 * groups, "measured_4": med_rss(t4),
            "scan": scan, "repartition": pair * 2 * groups,
            "purge_q3": med_rss(purge3) - med_rss(base), "purge_q1": med_rss(purge1) - med_rss(q1o),
            "stream_mb": f1["slope_kb"] / 1024 if f1 else float("nan"),
            "level": med_rss(base),
        }

    lines += ["", "**Decomposition of the excess of the deduplication at 1200 files, per series** "
              "(MB). The per-stream cost is the slope of Q1; the per-pair cost is the increase "
              "from 2 to 8 outputs divided by the pairs added, so it is derived from that increase "
              "and not confirmed independently of it; applying it to the pairs at 2 outputs "
              "assumes no fixed cost per input or per output.", "",
              "| series | streams | excess over the original | per stream | scan share | per pair | "
              "repartition share at 2 outputs | not attributed | 4 outputs, predicted / measured | "
              "purge 0, change for Q3 | for Q1 |", "|" + "---|" * 11]
    for s in series:
        d = per_series[s]
        rest = d["excess"] - d["scan"] - d["repartition"]
        d["rest"] = rest
        lines.append(f"| {s} | {d['groups']} | {d['excess']:.0f} | {d['stream_mb']:.2f} | {d['scan']:.0f} | "
                     f"{d['pair_mb']:.2f} | {d['repartition']:.0f} | {rest:.0f} | "
                     f"{d['predicted_4']:.0f} / {d['measured_4']:.0f} | {d['purge_q3']:+.0f} | {d['purge_q1']:+.0f} |")

    def across(key):
        v = [per_series[s][key] for s in series if not isnan(per_series[s][key])]
        return (min(v), max(v)) if v else (float("nan"), float("nan"))

    stream, pair, rest = across("stream_mb"), across("pair_mb"), across("rest")
    excess, level = across("excess"), across("level")
    added = across("added_2_to_8")
    lowered = sum(1 for s in series if per_series[s]["purge_q3"] < 0)
    purge = across("purge_q3")
    q2 = [fits[s, "Q2"] for s in series if fits[s, "Q2"]]
    flat = sum(1 for f in q2 if f["lo"] <= 0 <= f["hi"])
    lines += ["", f"Over the {len(series)} series:", "",
              f"- the scan holds {span(*stream)} MB per active read stream (slope of the `ORDER BY` "
              f"query, which reads all eight columns); with three columns read (Q2) the interval of "
              f"the slope includes zero in {flat} of {len(q2)} series;",
              f"- going from 2 to 8 outputs adds {span(added[0] / 1024, added[1] / 1024, 1)} GB, that is "
              f"{span(*pair)} MB per pair of input and output partition;",
              f"- the median RSS of the deduplication is {span(level[0] / 1024, level[1] / 1024, 1)} GB, "
              f"{span(excess[0] / 1024, excess[1] / 1024, 1)} GB above the original plan, and what the scan "
              f"and the repartition shares leave unattributed ranges from {rest[0]:.0f} to {rest[1]:.0f} MB;",
              f"- eager purging by the allocator lowers the median RSS of the deduplication in "
              f"{lowered} of {len(series)} series (changes from {purge[0]:+.0f} to {purge[1]:+.0f} MB).", ""]
    figures += [("many_streams_mb_per_stream", span(*stream)),
                ("many_streams_mb_per_pair", span(*pair)),
                ("many_streams_added_2_to_8_outputs_gb", span(added[0] / 1024, added[1] / 1024, 1)),
                ("many_streams_rss_gb", span(level[0] / 1024, level[1] / 1024, 1)),
                ("many_streams_excess_gb", span(excess[0] / 1024, excess[1] / 1024, 1)),
                ("many_streams_not_attributed_mb", f"{rest[0]:.0f} to {rest[1]:.0f}"),
                ("many_streams_purge_lowers_in", f"{lowered} of {len(series)} series"),
                ("many_streams_purge_change_mb", f"{purge[0]:+.0f} to {purge[1]:+.0f}")]
    return "\n".join(lines)


# ---------------------------------------------------------------- depth

def group_shares(manifest):
    """Share of the rows in every ordered group, from a dataset manifest."""
    groups, rows = None, {}
    for line in Path(manifest).read_text().splitlines():
        if line.startswith("# groups_by_bounds"):
            groups = ast.literal_eval(line.split("\t")[2])
        elif line and not line.startswith("#") and line.split("\t")[0].isdigit():
            parts = line.split("\t")
            rows[int(parts[0])] = int(parts[1])
    total = sum(rows.values())
    return [sum(rows[i] for i in g) / total for g in groups]


def depth(out, matrix, manifests, figures):
    path = Path(out) / "results.tsv"
    if not path.is_file():
        return missing("Depth 1 against depth 2: backpressure", out)
    rows = read_tsv(path)
    sizes = sorted({int(r["batch_size"]) for r in rows if r["kind"] == "batch"})
    lines = ["### Depth 1 against depth 2: backpressure", "",
             "`experiments/depth/run.py`: the deduplication with two ordered groups whose key "
             "ranges are disjoint (depth 1) and overlapping (depth 2), same plan in both, 256 MB, "
             "six runs per configuration. Wall and CPU time of the whole process; cores are CPU "
             "over wall. Means over the completed runs, standard deviation in parentheses.", "",
             "| batch size | depth 1: completed, wall s, cores | depth 2: completed, wall s, cores | "
             "gap in cores |", "|---|---|---|---|"]
    gaps, fails = {}, {}

    def shown(sel):
        ok = completed(sel)
        return (f"{n_of(sel)}, {num(mean(ok, 'wall_s'))} ({num(stdev(ok, 'wall_s'))}), "
                f"{num(mean(ok, 'cores'), 2)} ({num(stdev(ok, 'cores'), 2)})")

    for size in sizes:
        d1 = select(rows, kind="batch", batch_size=size, depth=1)
        d2 = select(rows, kind="batch", batch_size=size, depth=2)
        gaps[size] = mean(completed(d2), "cores") - mean(completed(d1), "cores")
        fails[size] = len(d1) + len(d2) - len(completed(d1)) - len(completed(d2))
        lines.append(f"| {size} | {shown(d1)} | {shown(d2)} | {num(gaps[size], 2)} |")
    default = 8192 if 8192 in gaps else sizes[0]
    closed = next((s for s in sizes if abs(gaps[s]) < 0.1), None)
    failing = [(s, fails[s], len(select(rows, kind='batch', batch_size=s))) for s in sizes if fails[s]]
    d1w = mean(completed(select(rows, kind="batch", batch_size=default, depth=1)), "wall_s")
    d2w = mean(completed(select(rows, kind="batch", batch_size=default, depth=2)), "wall_s")
    lines += ["", f"- At the default batch size ({default} rows) depth 1 takes {num(d1w)} s against "
              f"{num(d2w)} s at depth 2 and uses {num(gaps[default], 2)} cores less.",
              (f"- The gap in cores is below 0.1 from {closed} rows per batch."
               if closed else "- The gap in cores stays above 0.1 at every batch size tried."),
              ("- Runs that failed for memory: " + "; ".join(
                  f"{n} of {total} at {s} rows" for s, n, total in failing) + "."
               if failing else "- No run failed."), "",
              "Layout probes at the default batch size:", "",
              "| configuration | completed | scan groups | wall s | CPU s | cores |", "|---|---|---|---|---|---|"]
    labels = {"d1-120-files": "depth 1, 120 files", "d2-120-files": "depth 2, 120 files",
              "d1-split-by-name": "depth 1, 12 files, split between the partitions by name"}
    for name, label in labels.items():
        sel = select(rows, name=name)
        ok = completed(sel)
        groups = ", ".join(sorted({r["scan_groups"] for r in ok})) or "-"
        lines.append(f"| {label} | {n_of(sel)} | {groups} | {num(mean(ok, 'wall_s'))} | "
                     f"{num(mean(ok, 'cpu_s'))} | {num(mean(ok, 'cores'), 2)} |")
    # two alternative explanations, checked on the manifests and on the matrix
    shares = {}
    for d in (1, 2):
        m = Path(manifests) / f"df-16919-partial-12-depth-{d}.tsv"
        shares[d] = max(group_shares(m)) if m.is_file() else float("nan")
    batches = {}
    results = Path(matrix) / "results.tsv"
    if results.is_file():
        mrows = [r for r in read_tsv(results) if r["result"] == "ok" and r["variant"] == "accept-groups"]
        for d in (1, 2):
            sel = [r for r in mrows if r["case"] == f"A2-depth-{d}"]
            if sel and "partial_agg_out_batches" in sel[0]:
                batches[d] = (sorted({r["partial_agg_out_batches"] for r in sel}),
                              sorted({r["repartition_out_batches"] for r in sel}))
    large = max(sizes)
    lines += ["", "Two other explanations, checked on the recorded data:", "",
              f"- unequal groups: the larger of the two groups holds {100 * shares[1]:.0f} percent of "
              f"the rows at depth 1 and {100 * shares[2]:.0f} percent at depth 2 (manifests); the "
              f"imbalance is the same at every batch size, while the gap in cores goes from "
              f"{num(gaps[sizes[0]], 2)} at {sizes[0]} rows to {num(gaps[closed], 2) if closed else num(gaps[large], 2)} "
              f"at {closed or large};"]
    if len(batches) == 2:
        lines.append(f"- more and smaller batches: in the matrix the partial aggregate emits "
                     f"{', '.join(batches[1][0])} batches at depth 1 and {', '.join(batches[2][0])} at depth 2, "
                     f"the repartition {', '.join(batches[1][1])} and {', '.join(batches[2][1])}.")
    lines.append("")
    figures += [("depth_gap_cores_by_batch_size", "; ".join(f"{s}: {num(gaps[s], 2)}" for s in sizes)),
                ("depth_default_batch_wall_s_depth1_depth2", f"{num(d1w)} / {num(d2w)}"),
                ("depth_failed_runs", "; ".join(f"{n} of {t} at {s}" for s, n, t in failing) or "none")]
    for size in sizes:
        d1 = completed(select(rows, kind="batch", batch_size=size, depth=1))
        d2 = completed(select(rows, kind="batch", batch_size=size, depth=2))
        figures.append((f"depth_batch_{size}_d1_wall_cores_d2_wall_cores",
                        f"{num(mean(d1, 'wall_s'))} {num(mean(d1, 'cores'), 2)} / "
                        f"{num(mean(d2, 'wall_s'))} {num(mean(d2, 'cores'), 2)}"))
    return "\n".join(lines)


# ---------------------------------------------------------------- open files

def open_files(out, figures):
    path = Path(out) / "results.tsv"
    if not path.is_file():
        return missing("The open-file limit", out)
    rows = read_tsv(path)
    limits = sorted({int(r["open_file_limit"]) for r in rows})
    lines = ["### The open-file limit", "",
             "`experiments/open-files/run.py`: the deduplication on 1200 files with total overlap, "
             "256 MB, with the open-file limit of the process set to each value. Completed runs "
             "of those tried.", "",
             "| open-file limit | original plan | ordered plan |", "|---|---|---|"]
    first = {}

    def shown(limit, variant):
        sel = select(rows, open_file_limit=limit, variant=variant)
        done = [r for r in sel if str(r["completed"]) == "1"]
        tried = [r for r in sel if str(r["completed"]) != ""]
        if not tried:
            return sel[0]["error"] if sel else "not run"
        if len(done) == len(tried) and variant not in first:
            first[variant] = limit
        errors = sorted({r["error"].split(" at path")[0] for r in tried if str(r["completed"]) == "0"})
        return f"{len(done)} of {len(tried)}" + (f" ({'; '.join(errors)})" if errors else "")

    for limit in limits:
        lines.append(f"| {limit} | {shown(limit, 'original')} | {shown(limit, 'accept-groups')} |")
    groups = sorted({r["scan_groups"] for r in rows if r["variant"] == "accept-groups" and r["scan_groups"]})

    def need(variant):
        return f"completes from a limit of {first[variant]}" if variant in first else "completes at no limit tried"

    lines += ["", f"With {', '.join(groups) or 'the'} ordered groups and two outputs, the ordered plan "
              f"{need('accept-groups')}; the original plan {need('original')}.", ""]
    figures += [("open_files_ordered_completes_from", str(first.get("accept-groups", "none"))),
                ("open_files_original_completes_from", str(first.get("original", "none")))]
    return "\n".join(lines)
