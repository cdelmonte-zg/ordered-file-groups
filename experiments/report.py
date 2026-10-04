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
    """A number at fixed precision; 'n/a' when there is none (no completed run)."""
    return "n/a" if x is None or isnan(x) else f"{x:.{digits}f}"


def pct(x):
    return "n/a" if x is None or isnan(x) else f"{x:.0f}"


def error_kinds(rows):
    """The distinct errors of the runs that did not complete, shortened."""
    kinds = sorted({(r.get("error") or "no error message").split(" at path")[0].split(":")[0]
                    for r in rows if str(r.get("ok", r.get("completed"))) == "0"})
    return "; ".join(kinds)


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

    def gain_of(shape, pool, variant, strings, base_strings):
        """Percent by which a variant is faster than the original with the given strings."""
        o = median(completed(cell(shape, pool, "original", base_strings)), "elapsed_s")
        a = median(completed(cell(shape, pool, variant, strings)), "elapsed_s")
        return 100 * (1 - a / o)

    def gain(shape, pool, strings):
        return gain_of(shape, pool, "accept-groups", strings, strings)

    runs = max(len(cell(sh, p, "accept-groups", "views")) for sh in ("S0", "S2") for p in ("128m", "512m"))
    ACC = "accept-groups-accounting"
    lines = ["### String views and the repartition's accounting", "",
             "`experiments/string-views/run.py`: the deduplication on the narrow strings (S0) and on "
             "the wide ones (S2), read as Arrow string views and as plain `Utf8`, both plans, "
             f"{runs} runs each. A third binary runs the ordered plan with string views and with "
             "the repartition's reservation changed to the bytes a slice holds for its own rows "
             "(`patch/slice-accounting.patch`): it changes the accounting, at the price of one pass "
             "over the views of every batch sent. Median "
             "elapsed seconds, with the median spills of the final aggregate in parentheses.", "",
             "| shape | pool | original, views | original, Utf8 | ordered, views | ordered, Utf8 | "
             "ordered, views, slice accounting |",
             "|---|---|---|---|---|---|---|"]
    cells = [(s, p) for s in ("S0", "S2") for p in ("128m", "512m")]
    columns = (("original", "views"), ("original", "utf8"), ("accept-groups", "views"),
               ("accept-groups", "utf8"), (ACC, "views"))
    for shape, pool in cells:
        lines.append(f"| {shape} | {pool} | " + " | ".join(
            shown(cell(shape, pool, v, s)) for v, s in columns) + " |")

    lines += ["", "Gain of the ordered plan over the original with the same strings (for the slice "
              "accounting, over the original with views):", "",
              "| shape | pool | with string views | with plain Utf8 | with views and slice accounting |",
              "|---|---|---|---|---|"]
    for shape, pool in cells:
        lines.append(f"| {shape} | {pool} | {pct(gain(shape, pool, 'views'))} % | "
                     f"{pct(gain(shape, pool, 'utf8'))} % | "
                     f"{pct(gain_of(shape, pool, ACC, 'views', 'views'))} % |")

    def med(variant, strings, key):
        return median(completed(cell("S2", "128m", variant, strings)), key)

    gv, gu = gain("S2", "128m", "views"), gain("S2", "128m", "utf8")
    narrow = gain("S0", "128m", "views")
    failed = len(cell("S2", "128m", "original", "views")) - len(completed(cell("S2", "128m", "original", "views")))
    lines += ["", f"- On the wide strings at 128 MB the ordered plan gains {pct(gv)} percent with "
              f"string views and {pct(gu)} percent with plain `Utf8`; on the narrow strings, with "
              f"views, {pct(narrow)} percent.",
              f"- The final aggregate of the ordered plan spills {num(med('accept-groups', 'views', 'final_agg_spills'), 0)} "
              f"times with views and {num(med('accept-groups', 'utf8', 'final_agg_spills'), 0)} with plain strings.",
              f"- The order-preserving repartition of the ordered plan reports "
              f"{num(med('accept-groups', 'views', 'repartition_out_mb'), 0)} MB of output with views and "
              f"{num(med('accept-groups', 'utf8', 'repartition_out_mb'), 0)} with plain strings; the original "
              f"plan's repartition {num(med('original', 'views', 'repartition_out_mb'), 0)} and "
              f"{num(med('original', 'utf8', 'repartition_out_mb'), 0)}.",
              f"- With the views kept and only the accounting changed, the ordered plan takes "
              f"{num(med(ACC, 'views', 'elapsed_s'))} s, a gain of "
              f"{pct(gain_of('S2', '128m', ACC, 'views', 'views'))} percent; its final aggregate spills "
              f"{num(med(ACC, 'views', 'final_agg_spills'), 0)} times and its repartition "
              f"{num(med(ACC, 'views', 'repartition_spills'), 0)}, against "
              f"{num(med('accept-groups', 'views', 'final_agg_spills'), 0)} and "
              f"{num(med('accept-groups', 'views', 'repartition_spills'), 0)} with the engine's accounting.",
              f"- The views cost the original plan too: its sort spills "
              f"{num(med('original', 'views', 'sort_spills'), 0)} times with views and "
              f"{num(med('original', 'utf8', 'sort_spills'), 0)} with plain strings, its median is "
              f"{num(med('original', 'views', 'elapsed_s'))} against {num(med('original', 'utf8', 'elapsed_s'))} s"
              + (f", and {failed} of its {len(cell('S2', '128m', 'original', 'views'))} runs with views failed."
                 if failed else "."), ""]
    figures += [("string_test_gain_views_S2_128_pct", pct(gv)),
                ("string_test_gain_utf8_S2_128_pct", pct(gu)),
                ("string_test_gain_views_S0_128_pct", pct(narrow)),
                ("string_test_ordered_utf8_s", num(med('accept-groups', 'utf8', 'elapsed_s'))),
                ("string_test_original_utf8_s", num(med('original', 'utf8', 'elapsed_s'))),
                ("string_test_ordered_views_s", num(med('accept-groups', 'views', 'elapsed_s'))),
                ("string_test_original_views_s", num(med('original', 'views', 'elapsed_s'))),
                ("string_test_repartition_out_views_mb", num(med('accept-groups', 'views', 'repartition_out_mb'), 0)),
                ("string_test_repartition_out_utf8_mb", num(med('accept-groups', 'utf8', 'repartition_out_mb'), 0)),
                ("string_test_slice_accounting_views_s", num(med(ACC, 'views', 'elapsed_s'))),
                ("string_test_gain_slice_accounting_S2_128_pct", pct(gain_of('S2', '128m', ACC, 'views', 'views'))),
                ("string_test_slice_accounting_final_spills", num(med(ACC, 'views', 'final_agg_spills'), 0))]
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
        for q, label in (("Q0", "Q0, the scan with nothing above it"), ("Q1", "Q1, `ORDER BY`: scan and merge"),
                         ("Q2", "Q2, `GROUP BY` on the sort key, 3 columns read"),
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
        f0, f1 = fits[s, "Q0"], fits[s, "Q1"]
        scan = f1["slope_kb"] / 1024 * groups if f1 else float("nan")
        per_series[s] = {
            "groups": groups, "excess": med_rss(base) - original, "pair_mb": pair,
            "added_2_to_8": med_rss(t8) - med_rss(base),
            "predicted_4": med_rss(base) + pair * 2 * groups, "measured_4": med_rss(t4),
            "scan": scan, "repartition": pair * 2 * groups,
            "purge_q3": med_rss(purge3) - med_rss(base), "purge_q1": med_rss(purge1) - med_rss(q1o),
            "stream_mb": f1["slope_kb"] / 1024 if f1 else float("nan"),
            "bare_mb": f0["slope_kb"] / 1024 if f0 else float("nan"),
            "level": med_rss(base),
        }

    # streams crossed with outputs: is the growth with the outputs the same at
    # every number of streams (a cost per output) or proportional to the streams
    # (a cost per pair)?
    lines += ["", "**E2, streams crossed with outputs.** For every number of streams, the slope of "
              "the peak RSS of the deduplication on the outputs (2, 4, 8), in MB per output, and "
              "the same divided by the streams, in MB per pair. A cost per output would give the "
              "same slope at every number of streams; a cost per pair of input and output a slope "
              "proportional to the streams.", "",
              "| series | " + " | ".join(f"{f} files: streams, MB per output, MB per pair" for f in files_list) + " |",
              "|" + "---|" * (len(files_list) + 1)]
    crossing = {}
    for s in series:
        cells = []
        for f in files_list:
            xs, ys, groups = [], [], 0
            for target, sel in ((2, e1(s, "Q3", f, "accept-groups")),
                                (4, select(rows, series=s, name=f"E2-{f}-Q3-t4")),
                                (8, select(rows, series=s, name=f"E2-{f}-Q3-t8"))):
                for r in sel:
                    xs.append(target)
                    ys.append(float(r["rss_mb"]))
                    groups = int(r["groups"])
            per_output = slope(xs, ys) if len(set(xs)) == 3 else None
            crossing[s, f] = (groups, per_output)
            cells.append("n/a" if per_output is None or not groups else
                         f"{groups}, {per_output:.0f}, {per_output / groups:.2f}")
        lines.append(f"| {s} | " + " | ".join(cells) + " |")

    lines += ["", "**An exploratory model of the excess of the deduplication at 1200 files, per "
              "series** (MB). It is a model, not a measurement of operators: the RSS is the peak "
              "of the whole process, the peaks of two runs can fall in different phases, and "
              "differences of peaks are not the memory of an operator. The per-stream term is the "
              "slope of the `ORDER BY` query (Q1); the per-pair term is the increase from 2 to 8 outputs "
              "divided by the pairs added; applying it to the pairs at 2 outputs assumes no fixed "
              "cost per input or per output. What the two terms leave is the residual of the "
              "model, not memory observed separately.", "",
              "| series | streams | excess over the original | per stream | per-stream term | per pair | "
              "per-pair term at 2 outputs | residual | 4 outputs, predicted / measured | "
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
    bare = across("bare_mb")
    excess, level = across("excess"), across("level")
    added = across("added_2_to_8")
    lowered = sum(1 for s in series if per_series[s]["purge_q3"] < 0)
    purge = across("purge_q3")
    q2 = [fits[s, "Q2"] for s in series if fits[s, "Q2"]]
    flat = sum(1 for f in q2 if f["lo"] <= 0 <= f["hi"])
    # how the slope on the outputs scales with the streams, smallest against largest file count
    factors = []
    for s in series:
        (n_lo, o_lo), (n_hi, o_hi) = crossing[s, files_list[0]], crossing[s, files_list[-1]]
        if o_lo and o_hi and n_lo:
            factors.append((o_hi / o_lo, n_hi / n_lo))
    lines += ["", f"Over the {len(series)} series:", "",
              f"- with the `ORDER BY` query (Q1, a merge pulling from the streams, all eight "
              f"columns read) the excess RSS grows by {span(*stream)} MB per stream; with nothing "
              f"above the scan (Q0, all the streams driven at once) by {span(*bare)} MB per stream; "
              f"the two differ in how the streams are driven, so their difference is not the cost "
              f"of the merge; with three columns read (Q2) the interval of the slope includes zero "
              f"in {flat} of {len(q2)} series;",
              f"- at 1200 files, going from 2 to 8 outputs adds {span(added[0] / 1024, added[1] / 1024, 1)} GB, "
              f"which divided by the pairs added is {span(*pair)} MB per pair of input and output;"]
    if factors:
        slope_factor = (min(f[0] for f in factors), max(f[0] for f in factors))
        stream_factor = factors[0][1]
        lines.append(
            f"- from {files_list[0]} to {files_list[-1]} files the streams grow by a factor of "
            f"{stream_factor:.1f} and the slope on the outputs by a factor of {span(*slope_factor, 1)}: "
            f"a cost per output alone would give a factor of 1, a cost per pair alone the factor of the streams;")
    lines += [f"- the median RSS of the deduplication is {span(level[0] / 1024, level[1] / 1024, 1)} GB, "
              f"{span(excess[0] / 1024, excess[1] / 1024, 1)} GB above the original plan, and the residual "
              f"of the model ranges from {rest[0]:.0f} to {rest[1]:.0f} MB;",
              f"- eager purging by the allocator lowers the median RSS of the deduplication in "
              f"{lowered} of {len(series)} series (changes from {purge[0]:+.0f} to {purge[1]:+.0f} MB).", ""]
    figures += [("many_streams_mb_per_stream", span(*stream)),
                ("many_streams_mb_per_pair", span(*pair)),
                ("many_streams_added_2_to_8_outputs_gb", span(added[0] / 1024, added[1] / 1024, 1)),
                ("many_streams_rss_gb", span(level[0] / 1024, level[1] / 1024, 1)),
                ("many_streams_excess_gb", span(excess[0] / 1024, excess[1] / 1024, 1)),
                ("many_streams_model_residual_mb", f"{rest[0]:.0f} to {rest[1]:.0f}"),
                ("many_streams_mb_per_stream_scan_driven_at_once", span(*bare)),
                ("many_streams_slope_on_outputs_factor", span(*slope_factor, 1) if factors else "n/a"),
                ("many_streams_streams_factor", f"{factors[0][1]:.1f}" if factors else "n/a"),
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
    per_config = max(len(select(rows, name=n)) for n in {r["name"] for r in rows})
    lines = ["### Depth 1 against depth 2: backpressure", "",
             "`experiments/depth/run.py`: the deduplication with two ordered groups whose key "
             "ranges are disjoint (depth 1) and overlapping (depth 2), same plan in both, 256 MB, "
             f"{per_config} runs per configuration. Wall and CPU time of the whole process; cores are CPU "
             "over wall. Means over the completed runs, standard deviation in parentheses.", "",
             "| batch size | depth 1: completed, wall s, CPU s, cores | "
             "depth 2: completed, wall s, CPU s, cores | gap in cores | "
             "send time of the repartition's inputs, depth 1 / depth 2, s |", "|---|---|---|---|---|"]
    gaps, fails, send = {}, {}, {}

    def shown(sel):
        ok = completed(sel)
        return (f"{n_of(sel)}, {num(mean(ok, 'wall_s'))} ({num(stdev(ok, 'wall_s'))}), "
                f"{num(mean(ok, 'cpu_s'), 2)}, {num(mean(ok, 'cores'), 2)} ({num(stdev(ok, 'cores'), 2)})")

    for size in sizes:
        d1 = select(rows, kind="batch", batch_size=size, depth=1)
        d2 = select(rows, kind="batch", batch_size=size, depth=2)
        gaps[size] = mean(completed(d2), "cores") - mean(completed(d1), "cores")
        fails[size] = len(d1) + len(d2) - len(completed(d1)) - len(completed(d2))
        send[size] = (mean(completed(d1), "repartition_send_s"), mean(completed(d2), "repartition_send_s"))
        lines.append(f"| {size} | {shown(d1)} | {shown(d2)} | {num(gaps[size], 2)} | "
                     f"{num(send[size][0], 2)} / {num(send[size][1], 2)} |")
    default = 8192 if 8192 in gaps else sizes[0]
    # the smallest size from which the gap stays below 0.1 at every larger size tried
    closed = next((s for s in sizes if all(abs(gaps[t]) < 0.1 for t in sizes if t >= s)), None)
    failing = [(s, fails[s], len(select(rows, kind='batch', batch_size=s)),
                error_kinds(select(rows, kind='batch', batch_size=s))) for s in sizes if fails[s]]
    d1w = mean(completed(select(rows, kind="batch", batch_size=default, depth=1)), "wall_s")
    d2w = mean(completed(select(rows, kind="batch", batch_size=default, depth=2)), "wall_s")
    lines += ["", f"- At the default batch size ({default} rows) depth 1 takes {num(d1w)} s against "
              f"{num(d2w)} s at depth 2 and uses {num(gaps[default], 2)} cores less.",
              f"- The time the repartition's inputs spend sending their batches to the outputs "
              f"(the `send_time` metric, summed over the inputs) is {num(send[default][0], 2)} s at depth 1 "
              f"against {num(send[default][1], 2)} s at depth 2 at the default batch size, and "
              f"{num(send[sizes[0]][0], 2)} against {num(send[sizes[0]][1], 2)} s at {sizes[0]} rows.",
              (f"- The gap in cores is below 0.1 at {closed} rows per batch and at every larger size tried."
               if closed else "- The gap in cores does not stay below 0.1 up to the largest batch size tried."),
              ("- Runs that did not complete: " + "; ".join(
                  f"{n} of {total} at {s} rows ({kinds})" for s, n, total, kinds in failing) + "."
               if failing else "- Every run completed."), "",
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
                ("depth_failed_runs", "; ".join(f"{n} of {t} at {s}" for s, n, t, _ in failing) or "none"),
                ("depth_default_batch_send_s_depth1_depth2", f"{num(send[default][0], 2)} / {num(send[default][1], 2)}")]
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
    complete = {}                      # (limit, variant) -> every run tried completed

    def shown(limit, variant):
        sel = select(rows, open_file_limit=limit, variant=variant)
        done = [r for r in sel if str(r["completed"]) == "1"]
        tried = [r for r in sel if str(r["completed"]) != ""]
        if not tried:
            return sel[0]["error"] if sel else "not run"
        complete[limit, variant] = len(done) == len(tried)
        errors = sorted({r["error"].split(" at path")[0] for r in tried if str(r["completed"]) == "0"})
        return f"{len(done)} of {len(tried)}" + (f" ({'; '.join(errors)})" if errors else "")

    for limit in limits:
        lines.append(f"| {limit} | {shown(limit, 'original')} | {shown(limit, 'accept-groups')} |")
    groups = sorted({r["scan_groups"] for r in rows if r["variant"] == "accept-groups" and r["scan_groups"]})

    first = {}
    for variant in ("original", "accept-groups"):
        tried = [l for l in limits if (l, variant) in complete]
        # the smallest limit from which every larger limit tried completes too
        ok = [l for l in tried if all(complete[t, variant] for t in tried if t >= l)]
        if ok:
            first[variant] = ok[0]

    def need(variant):
        return (f"completes at a limit of {first[variant]} and at every larger one tried"
                if variant in first else "does not complete reliably at any limit tried")

    lines += ["", f"With {', '.join(groups) or 'the'} ordered groups and two outputs, the ordered plan "
              f"{need('accept-groups')}; the original plan {need('original')}.", ""]
    figures += [("open_files_ordered_completes_from", str(first.get("accept-groups", "none"))),
                ("open_files_original_completes_from", str(first.get("original", "none")))]
    return "\n".join(lines)


# ---------------------------------------------------------------- process

def process(out, figures):
    path = Path(out) / "results.tsv"
    if not path.is_file():
        return missing("The process from outside: RSS and open files over time", out)
    rows = read_tsv(path)
    queries = {"Q3": "deduplication", "Q1": "`ORDER BY` only"}
    plans = {"original": "original", "accept-groups": "ordered"}
    keys = ("peak_rss_mb", "peak_at_share_of_run", "parquet_open_at_peak", "max_parquet_open",
            "rss_mb_when_most_parquet_open", "max_temp_open", "max_descriptors")
    lines = ["### The process from outside: RSS and open files over time", "",
             "`experiments/process/run.py`: 1200 files with total overlap, 256 MB. While a query "
             "runs, `/proc/<pid>` is sampled for the resident memory and for the open descriptors "
             "by kind. Nothing in the engine is changed. Medians of the completed runs; the "
             "sampling takes CPU, so the times of these runs are not comparable with the matrix.", "",
             "| query | plan | completed | peak RSS, MB | the peak falls at this share of the run | "
             "Parquet files open at the peak | most Parquet files open at once | RSS then, MB | "
             "most temporary files open | most descriptors |", "|" + "---|" * 10]
    seen = {}
    for query, qlabel in queries.items():
        for variant, plabel in plans.items():
            sel = select(rows, query=query, variant=variant)
            ok = completed(sel)
            v = seen[query, variant] = {k: median(ok, k) for k in keys}
            lines.append(f"| {qlabel} | {plabel} | {n_of(sel)} | {num(v['peak_rss_mb'], 0)} | "
                         f"{num(v['peak_at_share_of_run'], 2)} | {num(v['parquet_open_at_peak'], 0)} | "
                         f"{num(v['max_parquet_open'], 0)} | {num(v['rss_mb_when_most_parquet_open'], 0)} | "
                         f"{num(v['max_temp_open'], 0)} | {num(v['max_descriptors'], 0)} |")
            for key in keys:
                figures.append((f"process_{query}_{variant}_{key}",
                                num(v[key], 2 if "share" in key else 0)))
    a = seen["Q3", "accept-groups"]
    lines += ["", f"In the deduplication the ordered plan holds up to {num(a['max_parquet_open'], 0)} "
              f"Parquet files and {num(a['max_temp_open'], 0)} temporary files open at once, "
              f"{num(a['max_descriptors'], 0)} descriptors in all. Its resident memory is "
              f"{num(a['rss_mb_when_most_parquet_open'], 0)} MB when the most data files are open "
              f"and peaks at {num(a['peak_rss_mb'], 0)} MB, at {num(100 * a['peak_at_share_of_run'], 0)} "
              f"percent of the run, with {num(a['parquet_open_at_peak'], 0)} data files open then.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- huge pages

def huge_pages(out, figures):
    path = Path(out) / "results.tsv"
    if not path.is_file():
        return missing("How much of the resident memory is transparent huge pages", out)
    rows = read_tsv(path)
    names = []
    for r in rows:
        if r["name"] not in names:
            names.append(r["name"])
    plan = {"original": "original", "accept-groups": "ordered"}
    lines = ["### How much of the resident memory is transparent huge pages", "",
             "`experiments/huge-pages/run.py`: the many-stream cases as the machine is set and with "
             "transparent huge pages switched off for the measured process alone "
             "(`prctl(PR_SET_THP_DISABLE)`). The peak RSS counts whole pages: a 2 MB page is "
             "resident as soon as one byte of it is touched. Medians of the completed runs.", "",
             "| case | plan | completed | peak RSS as set, MB | peak RSS without huge pages, MB | "
             "elapsed as set, s | elapsed without, s | minor faults as set | without |",
             "|" + "---|" * 9]
    med = {}
    for name in names:
        sel = select(rows, name=name)
        on, off = completed(select(sel, huge_pages="as set")), completed(select(sel, huge_pages="off"))
        med[name] = {"on": median(on, "rss_mb"), "off": median(off, "rss_mb"),
                     "t_on": median(on, "elapsed_s"), "t_off": median(off, "elapsed_s")}
        lines.append(f"| {sel[0]['case']} | {plan[sel[0]['variant']]} | {n_of(sel)} | "
                     f"{num(med[name]['on'], 0)} | {num(med[name]['off'], 0)} | "
                     f"{num(med[name]['t_on'])} | {num(med[name]['t_off'])} | "
                     f"{num(median(on, 'minor_faults'), 0)} | {num(median(off, 'minor_faults'), 0)} |")

    def excess(files, mode):
        return med[f"dedup-{files}-accept-groups"][mode] - med[f"dedup-{files}-original"][mode]

    o, a = med["dedup-1200-original"], med["dedup-1200-accept-groups"]
    t8 = med["dedup-1200-accept-groups-t8"]
    # whether huge pages were in use as the machine is set: with them a fault
    # brings in 2 MB, so the same memory takes far fewer faults
    big = completed(select(rows, name="dedup-1200-accept-groups"))
    faults_on = median(select(big, huge_pages="as set"), "minor_faults")
    faults_off = median(select(big, huge_pages="off"), "minor_faults")
    in_use = faults_off > 2 * faults_on
    lines += ["", (f"- As the machine is set the ordered plan at 1200 files takes {num(faults_on, 0)} minor "
                   f"page faults, without huge pages {num(faults_off, 0)}: huge pages are in use in the "
                   f"first arm." if in_use else
                   f"- The minor page faults are about the same in the two arms ({num(faults_on, 0)} and "
                   f"{num(faults_off, 0)}): huge pages were NOT in use as the machine is set, and the "
                   f"two arms measure the same thing.")]
    lines += [f"- With about 1200 ordered streams the ordered plan peaks at {num(a['on'], 0)} MB as "
              f"the machine is set and at {num(a['off'], 0)} MB without huge pages; the original plan "
              f"at {num(o['on'], 0)} and {num(o['off'], 0)} MB. The excess of the ordered plan over the "
              f"original is {num(excess(1200, 'on'), 0)} MB with huge pages and "
              f"{num(excess(1200, 'off'), 0)} MB without.",
              "- The same excess at 150, 300, 600 and 1200 files: "
              + ", ".join(num(excess(f, "on"), 0) for f in (150, 300, 600, 1200)) + " MB with huge pages, "
              + ", ".join(num(excess(f, "off"), 0) for f in (150, 300, 600, 1200)) + " MB without.",
              f"- Going from 2 to 8 outputs at 1200 files adds {num(t8['on'] - a['on'], 0)} MB with huge "
              f"pages and {num(t8['off'] - a['off'], 0)} MB without.",
              f"- Elapsed time of the ordered plan at 1200 files: {num(a['t_on'])} s as set, "
              f"{num(a['t_off'])} s without huge pages; of the original, {num(o['t_on'])} and "
              f"{num(o['t_off'])} s.", ""]
    figures += [("huge_pages_ordered_1200_rss_mb_as_set", num(a["on"], 0)),
                ("huge_pages_ordered_1200_rss_mb_without", num(a["off"], 0)),
                ("huge_pages_original_1200_rss_mb_as_set", num(o["on"], 0)),
                ("huge_pages_original_1200_rss_mb_without", num(o["off"], 0)),
                ("huge_pages_excess_1200_mb_as_set", num(excess(1200, "on"), 0)),
                ("huge_pages_excess_1200_mb_without", num(excess(1200, "off"), 0)),
                ("huge_pages_added_2_to_8_outputs_mb_as_set", num(t8["on"] - a["on"], 0)),
                ("huge_pages_added_2_to_8_outputs_mb_without", num(t8["off"] - a["off"], 0)),
                ("huge_pages_ordered_1200_s_as_set", num(a["t_on"])),
                ("huge_pages_ordered_1200_s_without", num(a["t_off"]))]
    return "\n".join(lines)
