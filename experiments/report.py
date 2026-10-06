"""The sections of RESULTS.md on the experiments, computed from their results.tsv.

Every number and every qualitative word in these sections comes from the
recorded runs; nothing is written by hand. Called by scripts/make_report.py.
"""
import ast
import random
import statistics as st
from math import sqrt, isnan
from pathlib import Path

from common import read_tsv, rm

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
    return f"### {name}\n\nNot run: `{out}` has no results.tsv, or an empty one.\n"


def quartile_word(a_q1, a_q3, b_q1, b_q3):
    """How A stands to B by the quartiles of the elapsed time: the one rule of the report.

    'faster' when A's third quartile is below B's first, 'slower' when A's first
    is above B's third, 'within the quartiles' otherwise; 'not comparable' when
    either side has no completed run (a quartile that is NaN).
    """
    if any(isnan(q) for q in (a_q1, a_q3, b_q1, b_q3)):
        return "not comparable"
    if a_q3 < b_q1:
        return "faster"
    if a_q1 > b_q3:
        return "slower"
    return "within the quartiles"


def versus(word, name):
    """'faster than <name>', 'slower than <name>', 'within the quartiles of <name>'."""
    if word == "not comparable":
        return f"not comparable with {name} (no completed run)"
    return f"{word} of {name}" if word.startswith("within") else f"{word} than {name}"


def elapsed_quartiles(sel):
    """(q1, median, q3) of elapsed_s over the completed runs, NaN without one."""
    q = rm.quartiles(values(completed(sel), "elapsed_s"))
    return tuple(float("nan") if x == "" else float(x) for x in q)


def stands(a, b):
    """How the runs `a` stand to the runs `b` on elapsed_s, by quartile_word."""
    qa, qb = elapsed_quartiles(a), elapsed_quartiles(b)
    return quartile_word(qa[0], qa[2], qb[0], qb[2])


def timed(sel):
    """'median [q1..q3]' of elapsed_s over the completed runs; 'n/a' without one."""
    q1, med, q3 = elapsed_quartiles(sel)
    return "n/a" if isnan(med) else f"{med:.3f} [{q1:.3f}..{q3:.3f}]"


def less_or_more(a, o):
    """'12 percent less' or '7 percent more': the time a against the time o."""
    if isnan(a) or isnan(o):
        return "n/a"
    change = 100 * (1 - a / o)
    return f"{abs(change):.0f} percent {'less' if change >= 0 else 'more'}"


PLANS = {"original": "original", "accept-groups": "ordered",
         "original-target": "original, target raised"}


# ---------------------------------------------------------------- large pools

def large_pools(out, figures):
    title = "The base case with larger pools"
    path = Path(out) / "results.tsv"
    if not path.is_file() or not path.stat().st_size:
        return missing(title, out)
    rows = read_tsv(path)
    pools = list(dict.fromkeys(r["pool"] for r in rows))
    for r in rows:                         # a table written before the query was a column
        r.setdefault("query", "Q3")
    queries = list(dict.fromkeys(r["query"] for r in rows))
    names = {"Q3": "the deduplication", "Q5": "the deduplication without its `ORDER BY`"}

    lines = [f"### {title}", "",
             "`experiments/large-pools/run.py`: the base case with pools beyond those of the "
             "matrix (" + ", ".join(pools) + "), to reach a pool at which the final aggregate of "
             "the original plan does not spill; the deduplication, and the same query without its "
             "`ORDER BY`, in which no plan sorts. Medians of the completed runs, elapsed seconds "
             "with the quartiles.", "",
             "| query | pool | plan | completed | sorts / final aggregate | elapsed, s | "
             "peak RSS, MB | sort spills | final-aggregate spills | repartition spills |",
             "|" + "---|" * 10]

    def cell(query, pool, variant):
        return select(rows, query=query, pool=pool, variant=variant)

    def med(query, pool, variant, key):
        return median(completed(cell(query, pool, variant)), key)

    def shape_of(sel):
        return ", ".join(sorted({f"{r.get('sort_exec', '')} / {r.get('final_mode', '')}"
                                 for r in completed(sel)})) or "n/a"

    def sorts(query, pool):
        """Whether the completed runs of the original plan have a SortExec."""
        return any(str(r.get("sort_exec", "")) not in ("", "0")
                   for r in completed(cell(query, pool, "original")))

    for query in queries:
        for pool in pools:
            for variant, label in PLANS.items():
                sel = cell(query, pool, variant)
                lines.append(f"| {query} | {pool} | {label} | {n_of(sel)} | {shape_of(sel)} | "
                             f"{timed(sel)} | {num(med(query, pool, variant, 'rss_mb'), 0)} | "
                             f"{num(med(query, pool, variant, 'sort_spills'), 0)} | "
                             f"{num(med(query, pool, variant, 'final_agg_spills'), 0)} | "
                             f"{num(med(query, pool, variant, 'repartition_spills'), 0)} |")
    lines.append("")
    for query in queries:
        tag = "large_pools" if query == "Q3" else f"large_pools_{query}"
        lines += [f"For {names.get(query, query)}:", ""]
        free = []                  # per pool: the original's final aggregate does not spill
        for pool in pools:
            o_sel, a_sel = cell(query, pool, "original"), cell(query, pool, "accept-groups")
            o, a = med(query, pool, "original", "elapsed_s"), med(query, pool, "accept-groups", "elapsed_s")
            spills = med(query, pool, "original", "final_agg_spills")
            word = stands(a_sel, o_sel)
            complete = all(len(completed(x)) == len(x) > 0 for x in (o_sel, a_sel))
            free.append(complete and spills == 0)
            if isnan(o) or isnan(a):
                lines.append(f"- {pool}: no comparison, {n_of(o_sel)} runs of the original and "
                             f"{n_of(a_sel)} of the ordered plan completed.")
            else:
                sort = (f" and its sort {num(med(query, pool, 'original', 'sort_spills'), 0)}"
                        if sorts(query, pool) else "")
                free_runs = sum(float(r["final_agg_spills"] or "nan") == 0 for r in completed(o_sel))
                figures.append((f"{tag}_{pool}_original_runs_without_final_spills",
                                f"{free_runs} of {len(completed(o_sel))}"))
                lines.append(f"- {pool}: the ordered plan takes {num(a)} against {num(o)} s, "
                             f"{less_or_more(a, o)}, {versus(word, 'the original')}; the final "
                             f"aggregate of the original spills {num(spills, 0)} times in median "
                             f"and not at all in {free_runs} of its {len(completed(o_sel))} "
                             f"completed runs{sort}, the final "
                             f"aggregate of the ordered plan "
                             f"{num(med(query, pool, 'accept-groups', 'final_agg_spills'), 0)}.")
            figures += [(f"{tag}_{pool}_original_s", num(o)),
                        (f"{tag}_{pool}_ordered_s", num(a)),
                        (f"{tag}_{pool}_ordered_change", less_or_more(a, o)),
                        (f"{tag}_{pool}_ordered_against_original", word),
                        (f"{tag}_{pool}_original_final_spills", num(spills, 0)),
                        (f"{tag}_{pool}_original_sort_spills",
                         num(med(query, pool, "original", "sort_spills"), 0)
                         if sorts(query, pool) else "no sort"),
                        (f"{tag}_{pool}_original_rss_mb", num(med(query, pool, "original", "rss_mb"), 0)),
                        (f"{tag}_{pool}_ordered_rss_mb", num(med(query, pool, "accept-groups", "rss_mb"), 0)),
                        (f"{tag}_{pool}_target_s", num(med(query, pool, "original-target", "elapsed_s")))]
        # the smallest pool from which every larger pool run holds too
        first = next((i for i in range(len(pools)) if all(free[i:])), None)
        if first is None:
            lines.append("- There is no pool from which the final aggregate of the original plan "
                         "stays without spills in every completed cell: the gain and the spills "
                         "avoided are not told apart here.")
        else:
            pool = pools[first]
            word = stands(cell(query, pool, "accept-groups"), cell(query, pool, "original"))
            lines.append(f"- From {pool} on, at every pool run, the final aggregate of the original "
                         f"plan does not spill (median over all its runs, all completed). At {pool} "
                         f"the ordered plan is {versus(word, 'the original')} "
                         f"({num(med(query, pool, 'accept-groups', 'elapsed_s'))} against "
                         f"{num(med(query, pool, 'original', 'elapsed_s'))} s)."
                         + (f" The sort of the original spills "
                            f"{num(med(query, pool, 'original', 'sort_spills'), 0)} times there."
                            if sorts(query, pool) else " No plan of this query sorts."))
        figures.append((f"{tag}_original_final_spill_free_from",
                        pools[first] if first is not None else "none"))
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- target partitions

def target_partitions(out, figures):
    title = "Raising target_partitions when the groups are many"
    path = Path(out) / "results.tsv"
    if not path.is_file() or not path.stat().st_size:
        return missing(title, out)
    rows = read_tsv(path)
    cells = list(dict.fromkeys((r["files"], r["pool"]) for r in rows))
    raised = sorted({int(r["target_partitions"]) for r in rows if r["variant"] == "original-target"})
    base = sorted({int(r["target_partitions"]) for r in rows if r["variant"] in ("original", "accept-groups")})

    def cell(files, pool, variant):
        return select(rows, files=files, pool=pool, variant=variant)

    def groups_of(sel):
        return sorted({int(g) for g in ((r.get("scan_groups") or "") for r in completed(sel))
                       if g.isdigit()})

    def shape_of(sel):
        """'<SortExec count> / <mode of the final aggregate>' of the completed runs."""
        shapes = sorted({f"{r.get('sort_exec', '')} / {r.get('final_mode', '')}" for r in completed(sel)})
        return ", ".join(shapes) or "n/a"

    lines = [f"### {title}", "",
             "`experiments/target-partitions/run.py`: the files with total overlap. The original "
             "plan and the ordered plan run with the target at "
             + ", ".join(map(str, base)) + "; the third row is the original binary with "
             "`target_partitions` raised to the ordered groups the manifest of the dataset gives ("
             + ", ".join(map(str, raised)) + "); the fourth raises the target with "
             "`split_file_groups_by_statistics` off. Medians of the completed runs.", "",
             "| files | pool | plan | target | completed | file groups | sorts / final aggregate | "
             "elapsed, s | peak RSS, MB | final-aggregate spills | repartition spills | errors |",
             "|" + "---|" * 12]
    plans = {**PLANS, "original-target-split-off": "original, target raised, grouping off"}
    for files, pool in cells:
        for variant, label in plans.items():
            sel = cell(files, pool, variant)
            if not sel:
                continue
            ok = completed(sel)
            lines.append(f"| {files} | {pool} | {label} | {sel[0]['target_partitions']} | "
                         f"{n_of(sel)} | {', '.join(map(str, groups_of(sel))) or 'n/a'} | "
                         f"{shape_of(sel)} | {timed(sel)} | "
                         f"{num(median(ok, 'rss_mb'), 0)} | {num(median(ok, 'final_agg_spills'), 0)} | "
                         f"{num(median(ok, 'repartition_spills'), 0)} | {error_kinds(sel)} |")
    lines.append("")
    for files, pool in cells:
        t, a = cell(files, pool, "original-target"), cell(files, pool, "accept-groups")
        word = stands(t, a)
        text = f"- {files} files, {pool}: with the target raised {n_of(t)} runs complete"
        if completed(t):
            text += (f"; they take {num(median(completed(t), 'elapsed_s'))} s, "
                     f"{versus(word, 'the ordered plan')} ({num(median(completed(a), 'elapsed_s'))} s), "
                     f"with {num(median(completed(t), 'rss_mb'), 0)} MB of RSS against "
                     f"{num(median(completed(a), 'rss_mb'), 0)}")
            wanted = {int(r["target_partitions"]) for r in t}
            if set(groups_of(t)) != wanted:
                text += (f". The scan shows {', '.join(map(str, groups_of(t)))} file groups, not the "
                         "target: these runs are not the workaround")
        off = cell(files, pool, "original-target-split-off")
        if off:
            text += f". With the grouping by statistics off and the same target, {n_of(off)} complete"
            if completed(off):
                text += (f", in {num(median(completed(off), 'elapsed_s'))} s with "
                         f"{num(median(completed(off), 'rss_mb'), 0)} MB of RSS")
            figures += [(f"target_{files}_{pool}_split_off_completed", n_of(off)),
                        (f"target_{files}_{pool}_split_off_s", num(median(completed(off), "elapsed_s")))]
        lines.append(text + ".")
        figures += [(f"target_{files}_{pool}_completed", n_of(t)),
                    (f"target_{files}_{pool}_s", num(median(completed(t), "elapsed_s"))),
                    (f"target_{files}_{pool}_rss_mb", num(median(completed(t), "rss_mb"), 0)),
                    (f"target_{files}_{pool}_against_ordered", word),
                    (f"target_{files}_{pool}_ordered_completed", n_of(a)),
                    (f"target_{files}_{pool}_ordered_s", num(median(completed(a), "elapsed_s"))),
                    (f"target_{files}_{pool}_ordered_rss_mb", num(median(completed(a), "rss_mb"), 0))]
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- rows

def rows(out, figures):
    title = "More rows for the same files"
    path = Path(out) / "results.tsv"
    if not path.is_file() or not path.stat().st_size:
        return missing(title, out)
    table = read_tsv(path)
    # one dataset: (series, rows), with its prefixes and keys per prefix from the manifest
    sets = list(dict.fromkeys((r["series"], r["rows"], r["distinct_prefixes"],
                               f"{r['keys_per_prefix']} (largest {r['keys_per_prefix_max']})")
                              for r in table))

    def cell(series, size, kind, variant):
        return [r for r in select(table, series=series, pool_kind=kind, variant=variant)
                if r["rows"] == size]

    def med(series, size, kind, variant, key):
        return median(completed(cell(series, size, kind, variant)), key)

    def pool_of(series, size, kind):
        return next((r["pool"] for r in table if (r["series"], r["rows"], r["pool_kind"])
                     == (series, size, kind)), "n/a")

    def all_completed(sel):
        return len(completed(sel)) == len(sel) > 0

    lines = [f"### {title}", "",
             "`experiments/rows/run.py`: the base layout (twelve files, depth 4) with more rows, "
             "in two series. In `keys-per-prefix` the distinct (col_1, col_2) prefixes stay and "
             "the grouping keys under each grow; in `prefixes` the prefixes grow and the keys "
             "under each stay; `concentrated` puts the keys under few prefixes. Prefixes and keys "
             "per prefix (mean, and the largest prefix) are those of the manifests, counted on "
             "the source table and not on what reaches each partition of the final aggregate. The deduplication runs "
             "under a small pool and under a pool chosen for each size so that the original plan "
             "has room. Medians of the completed runs, elapsed seconds with the quartiles. What "
             "the plans reserve on these datasets is in the section on the traced runs.", "",
             "| series | rows | prefixes | keys per prefix | pool | plan | completed | elapsed, s | "
             "peak RSS, MB | sort spills | final-aggregate spills | repartition spills | errors |",
             "|" + "---|" * 13]
    for series, size, prefixes, per in sets:
        for kind in ("small", "large"):
            for variant, label in PLANS.items():
                sel = cell(series, size, kind, variant)
                lines.append(f"| {series} | {int(size):,} | {int(prefixes):,} | {per} | "
                             f"{pool_of(series, size, kind)} | {label} | {n_of(sel)} | {timed(sel)} | "
                             f"{num(med(series, size, kind, variant, 'rss_mb'), 0)} | "
                             f"{num(med(series, size, kind, variant, 'sort_spills'), 0)} | "
                             f"{num(med(series, size, kind, variant, 'final_agg_spills'), 0)} | "
                             f"{num(med(series, size, kind, variant, 'repartition_spills'), 0)} | "
                             f"{error_kinds(sel)} |")
    lines.append("")
    for series, size, prefixes, per in sets:
        tag = f"rows_{series}_{size}"
        figures += [(f"{tag}_prefixes", prefixes), (f"{tag}_keys_per_prefix", per)]
        for kind in ("small", "large"):
            o, a = cell(series, size, kind, "original"), cell(series, size, kind, "accept-groups")
            word = stands(a, o)
            to, ta = (med(series, size, kind, v, "elapsed_s") for v in ("original", "accept-groups"))
            so, sa = (med(series, size, kind, v, "final_agg_spills") for v in ("original", "accept-groups"))
            text = (f"- {series}, {int(size):,} rows ({int(prefixes):,} prefixes, {per} keys under "
                    f"each), {pool_of(series, size, kind)}: ")
            if isnan(to) or isnan(ta):
                text += (f"no comparison, {n_of(o)} runs of the original and {n_of(a)} of the "
                         "ordered plan completed.")
            else:
                text += (f"the ordered plan takes {num(ta)} against {num(to)} s, {less_or_more(ta, to)}, "
                         f"{versus(word, 'the original')}; final-aggregate spills {num(so, 0)} for the "
                         f"original and {num(sa, 0)} for the ordered plan; peak RSS "
                         f"{num(med(series, size, kind, 'accept-groups', 'rss_mb'), 0)} against "
                         f"{num(med(series, size, kind, 'original', 'rss_mb'), 0)} MB.")
                if kind == "large" and not (all_completed(o) and so == 0):
                    text += (" The final aggregate of the original still spills, or not all its "
                             "runs completed: this size does not tell the gain from the spills avoided.")
            lines.append(text)
            figures += [(f"{tag}_{kind}_pool", pool_of(series, size, kind)),
                        (f"{tag}_{kind}_original_s", num(to)), (f"{tag}_{kind}_ordered_s", num(ta)),
                        (f"{tag}_{kind}_ordered_change", less_or_more(ta, to)),
                        (f"{tag}_{kind}_ordered_against_original", word),
                        (f"{tag}_{kind}_original_final_spills", num(so, 0)),
                        (f"{tag}_{kind}_ordered_final_spills", num(sa, 0)),
                        (f"{tag}_{kind}_original_rss_mb",
                         num(med(series, size, kind, "original", "rss_mb"), 0)),
                        (f"{tag}_{kind}_ordered_rss_mb",
                         num(med(series, size, kind, "accept-groups", "rss_mb"), 0)),
                        (f"{tag}_{kind}_target_s",
                         num(med(series, size, kind, "original-target", "elapsed_s"))),
                        (f"{tag}_{kind}_original_completed", n_of(o)),
                        (f"{tag}_{kind}_target_completed",
                         n_of(cell(series, size, kind, "original-target")))]
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- causes

def causes(out, figures):
    title = "Why plans with many ordered groups fail or lose"
    path = Path(out) / "results.tsv"
    if not path.is_file() or not path.stat().st_size:
        return missing(title, out)
    rows = read_tsv(path)
    plan = {"original": "original", "accept-groups": "ordered"}

    def shape_of(sel):
        return ", ".join(sorted({f"{r.get('sort_exec', '')} / {r.get('final_mode', '')}"
                                 for r in completed(sel)})) or "n/a"

    def groups_of(sel):
        return ", ".join(sorted({r["scan_groups"] for r in completed(sel) if r["scan_groups"]},
                                key=int)) or "n/a"

    def table(part):
        keys = list(dict.fromkeys((r["change"], r["files"], r["pool"], r["binary"],
                                   r["target_partitions"], r["pool_type"], r["strings"])
                                  for r in rows if r["part"] == part))
        out = ["| what changes | files | pool | binary | target | pool type | strings | completed | "
               "file groups | sorts / final aggregate | elapsed, s | peak RSS, MB | "
               "final-aggregate spills | repartition spills | errors |", "|" + "---|" * 15]
        for change, files, pool, binary, target, pool_type, strings in keys:
            sel = select(rows, part=part, change=change, files=files, pool=pool, binary=binary,
                         target_partitions=target, pool_type=pool_type, strings=strings)
            ok = completed(sel)
            out.append(f"| {change} | {files} | {pool} | {plan[binary]} | {target} | {pool_type} | "
                       f"{strings} | {n_of(sel)} | {groups_of(sel)} | {shape_of(sel)} | {timed(sel)} | "
                       f"{num(median(ok, 'rss_mb'), 0)} | {num(median(ok, 'final_agg_spills'), 0)} | "
                       f"{num(median(ok, 'repartition_spills'), 0)} | {error_kinds(sel)} |")
        return out

    lines = [f"### {title}", "",
             "`experiments/causes/run.py`: from two configurations of the lab one thing changes at "
             "a time. Medians of the completed runs.", "",
             "W. The workaround on the files with total overlap that need 120 groups, and what "
             "changes from it: the pool, the strings, the outputs of the repartition (the ordered "
             "plan has the same ordered inputs; its target is the number of outputs).", ""]
    lines += table("W") + [""]

    def w(pool, change, **more):
        return select(rows, part="W", pool=pool, change=change, **more)

    for pool in dict.fromkeys(r["pool"] for r in rows if r["part"] == "W"):
        base, greedy, utf8 = w(pool, "none"), w(pool, "pool"), w(pool, "strings")
        outs = sorted({int(r["target_partitions"]) for r in w(pool, "outputs")})
        done = {o: n_of(w(pool, "outputs", target_partitions=o)) for o in outs}
        whole = [o for o in outs
                 if len(completed(w(pool, "outputs", target_partitions=o)))
                 == len(w(pool, "outputs", target_partitions=o)) > 0]
        lines.append(f"- {pool}: the workaround completes {n_of(base)} runs; with the greedy pool "
                     f"{n_of(greedy)}; with plain `Utf8` {n_of(utf8)}; the ordered plan with "
                     + ", ".join(f"{o} outputs {done[o]}" for o in outs) + ".")
        figures += [(f"causes_W_{pool}_workaround_completed", n_of(base)),
                    (f"causes_W_{pool}_greedy_completed", n_of(greedy)),
                    (f"causes_W_{pool}_utf8_completed", n_of(utf8)),
                    (f"causes_W_{pool}_most_outputs_all_completed",
                     str(max(whole)) if whole else "none")]
        figures += [(f"causes_W_{pool}_outputs_{o}_completed", done[o]) for o in outs]
    lines += ["", "S. The ordered plan at 128 MB as the ordered streams grow, with the original as "
              "reference, and at 1200 files with the pool and the strings changed.", ""]
    lines += table("S") + [""]

    def s_(files, binary, change):
        return select(rows, part="S", files=files, binary=binary, change=change)

    for files in dict.fromkeys(r["files"] for r in rows if r["part"] == "S"):
        a, o = s_(files, "accept-groups", "none"), s_(files, "original", "reference")
        if not a or not o:
            continue
        word = stands(a, o)
        ta, to = median(completed(a), "elapsed_s"), median(completed(o), "elapsed_s")
        lines.append(f"- {files} files: the ordered plan ({groups_of(a)} groups) takes {num(ta)} "
                     f"against {num(to)} s, {less_or_more(ta, to)}, {versus(word, 'the original')}; "
                     f"its final aggregate spills {num(median(completed(a), 'final_agg_spills'), 0)} "
                     f"times and its repartition {num(median(completed(a), 'repartition_spills'), 0)}; "
                     f"{n_of(a)} of its runs complete.")
        figures += [(f"causes_S_{files}_ordered_s", num(ta)), (f"causes_S_{files}_original_s", num(to)),
                    (f"causes_S_{files}_ordered_against_original", word),
                    (f"causes_S_{files}_ordered_final_spills",
                     num(median(completed(a), "final_agg_spills"), 0))]
    for change, label in (("pool", "the greedy pool"), ("strings", "plain `Utf8`")):
        files = next((r["files"] for r in rows if r["part"] == "S" and r["change"] == change), None)
        if files is None:
            continue
        a, o = s_(files, "accept-groups", change), s_(files, "original", change)
        word = stands(a, o)
        ta, to = median(completed(a), "elapsed_s"), median(completed(o), "elapsed_s")
        lines.append(f"- {files} files with {label}: the ordered plan takes {num(ta)} against "
                     f"{num(to)} s, {less_or_more(ta, to)}, {versus(word, 'the original')}; its "
                     f"final aggregate spills {num(median(completed(a), 'final_agg_spills'), 0)} "
                     f"times ({n_of(a)} and {n_of(o)} runs complete).")
        key = "greedy" if change == "pool" else "utf8"
        figures += [(f"causes_S_{files}_{key}_ordered_s", num(ta)),
                    (f"causes_S_{files}_{key}_original_s", num(to)),
                    (f"causes_S_{files}_{key}_ordered_against_original", word),
                    (f"causes_S_{files}_{key}_ordered_final_spills",
                     num(median(completed(a), "final_agg_spills"), 0))]
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- trace

# class of consumer -> the column of the operator that reports spills for it
SPILLS_OF = {"FinalHashAggregateStream": "final_agg_spills",
             "OrderedFinalAggregateStream": "final_agg_spills",
             "PartialHashAggregateStream": "partial_agg_spills",
             "OrderedPartialAggregateStream": "partial_agg_spills",
             "ExternalSorter": "sort_spills", "RepartitionExec": "repartition_spills"}


def read_trace(path):
    """A trace file of the traced binaries: {'pool': {...}, 'classes': {...}, 'events': [...]}."""
    pool, classes, events = {}, {}, []
    for line in Path(path).read_text().splitlines():
        f = line.split("\t")
        if f[0] == "pool":
            pool = {"fair": f[1] == "fair", "limit": int(f[2]) if f[2].isdigit() else None,
                    "peak_reserved": int(f[3]), "refusals": int(f[4]), "dropped": int(f[5])}
        elif f[0] == "class":
            classes[f[1]] = dict(zip(("consumers", "can_spill", "sum_of_peaks", "largest_peak",
                                      "class_peak", "refusals", "refused_bytes", "then_release",
                                      "ending_refused"), map(int, f[2:11])))
        elif f[0] == "event":
            # the fair pool gives a quota to the consumers that can spill; one that cannot is
            # refused when the whole pool has no room, and has no quota to be read against
            can_spill = f[4] == "1"
            events.append({"kind": f[2], "consumer": f[3], "can_spill": can_spill,
                           "bytes": int(f[5]), "held": int(f[6]),
                           "reserved": int(f[7]), "spillable": int(f[8]),
                           "unspillable": int(f[10]),
                           "quota": int(f[11]) if f[11] and can_spill else None, "top": f[12]})
    return {"pool": pool, "classes": classes, "events": events}


def class_name(consumer):
    """The class of a consumer as the wrapper names it: the index removed."""
    head, bracket, inside = consumer.partition("[")
    inside = "".join(c for c in inside.rstrip("]") if not c.isdigit()).strip()
    return f"{head}[{inside}]" if bracket and inside else head


def trace(out, figures):
    title = "What the memory pool grants and refuses"
    out = Path(out)
    path = out / "results.tsv"
    if not path.is_file() or not path.stat().st_size:
        return missing(title, out)
    rows = read_tsv(path)
    mb = 1 << 20

    def key_of(r):
        return (r["case"], r["binary"], r["target_partitions"], r["pool"], r["pool_type"])

    keys = list(dict.fromkeys(key_of(r) for r in rows))
    plan = {"original": "original", "accept-groups": "ordered"}

    def label(k):
        return f"{k[0]} | {plan[k[1]]} | {k[2]} | {k[3]} | {k[4]}"

    def sel(k, traced):
        return [r for r in rows if key_of(r) == k and str(r["traced"]) == str(traced)]

    def traces(k):
        return [(r, read_trace(out / r["trace_file"])) for r in sel(k, 1)
                if r["trace_file"] and (out / r["trace_file"]).is_file()]

    def med_of(xs):
        xs = [x for x in xs if x is not None]
        return st.median(xs) if xs else float("nan")

    head = "| case | plan | target | pool | pool type |"
    lines = [f"### {title}", "",
             "`experiments/trace/run.py`: every configuration with the binary of the lab and with "
             "the traced one (`patch/trace-pool.patch`), which wraps the memory pool and records "
             "what its consumers reserve and are refused. The wrapper forwards every call "
             "unchanged, but the lock it takes can change how the tasks interleave: the first "
             "table sets the two binaries side by side on what does not depend on time. No time "
             "of a traced run is used.", "",
             "Completed runs and median spills reported by the operators, plain / traced:", "",
             head + " completed | final aggregate | partial aggregate | sort | repartition |",
             "|" + "---|" * 10]
    same, differ, gaps = 0, [], []
    for k in keys:
        p, t = sel(k, 0), sel(k, 1)

        def pair(col):
            a, b = median(completed(p), col), median(completed(t), col)
            if not isnan(a) and not isnan(b):
                gaps.append((abs(a - b), max(a, b), f"{k[0]}, {plan[k[1]]}, {k[3]}, {k[4]}, "
                             f"{col.replace('_spills', '').replace('_', ' ')}: {a:g} / {b:g}"))
            return f"{num(a, 0)} / {num(b, 0)}"

        lines.append(f"| {label(k)} | {n_of(p)} / {n_of(t)} | {pair('final_agg_spills')} | "
                     f"{pair('partial_agg_spills')} | {pair('sort_spills')} | "
                     f"{pair('repartition_spills')} |")
        if len(completed(p)) == len(completed(t)):
            same += 1
        else:
            differ.append(f"{k[0]}, {plan[k[1]]}, {k[3]}, {k[4]} ({n_of(p)} against {n_of(t)})")
    lines += ["", f"- The two binaries complete the same number of runs in {same} of {len(keys)} "
              "configurations" + ("; they differ in: " + "; ".join(differ) if differ else "")
              + f". The median spills of an operator are equal in {sum(g[0] == 0 for g in gaps)} of "
              f"{len(gaps)} cells of the table where both binaries completed; the largest "
              "differences are: " + "; ".join(g[2] for g in sorted(gaps, reverse=True)[:4])
              + ". That and the spills above say how far the two are comparable on what was "
              "recorded; they do not show that the wrapper perturbs nothing. Where the two "
              "differ, the difference can be variation between runs or an effect of the wrapper, "
              "and what the traces say of that configuration is read with it.", ""]
    figures += [("trace_same_completions", f"{same} of {len(keys)}"),
                ("trace_equal_spill_medians", f"{sum(g[0] == 0 for g in gaps)} of {len(gaps)}"),
                ("trace_largest_spill_difference",
                 sorted(gaps, reverse=True)[0][2] if gaps else "n/a")]

    lines += ["Refusals, traced runs. The refusals and the peak of the pool are medians over the "
              "runs; the other columns count the refusals kept, over all the runs (a trace keeps "
              "the first and the last events of a run). A refusal is above its quota when what "
              "the consumer held plus what it asked exceeds the quota the wrapper computes at "
              "that moment; the request fitted the pool when what the pool reported as reserved "
              "plus the request did not exceed the limit. Only a consumer that can spill has a "
              "quota, and only under the fair pool: the refusals of the others are not counted in "
              "that column.", "",
              head + " refusals | refusals kept | above quota | the request fitted the pool | "
              "pool peak, MB |", "|" + "---|" * 10]
    exceptions = {}
    for k in keys:
        ts = traces(k)
        if not ts:
            lines.append(f"| {label(k)} | n/a | n/a | n/a | n/a | n/a |")
            continue
        above = below = kept = withq = 0
        for _, t in ts:
            for e in t["events"]:
                if e["kind"] != "refusal":
                    continue
                kept += 1
                fits = (t["pool"]["limit"] is not None
                        and e["reserved"] + e["bytes"] <= t["pool"]["limit"])
                below += fits
                if e["quota"] is not None:
                    withq += 1
                    over = e["held"] + e["bytes"] > e["quota"]
                    above += over
                    if not over:
                        where = (class_name(e["consumer"]),
                                 "the request fitted the pool" if fits else "the pool was full")
                        exceptions.setdefault(k, {}).setdefault(where, []).append(
                            (e["held"] + e["bytes"]) / e["quota"] if e["quota"] else float("nan"))
        refusals = med_of([t["pool"]["refusals"] for _, t in ts])
        peak = med_of([t["pool"]["peak_reserved"] / mb for _, t in ts])
        lines.append(f"| {label(k)} | {num(refusals, 0)} | {kept} | "
                     + (f"{above} of {withq}" if withq else "no quota") + f" | {below} of {kept} | "
                     f"{num(peak, 0)} |")
        tag = "trace_" + "_".join(k)
        figures += [(f"{tag}_refusals", num(refusals, 0)),
                    (f"{tag}_above_quota", f"{above} of {withq}" if withq else "no quota"),
                    (f"{tag}_request_fitted_pool", f"{below} of {kept}"),
                    (f"{tag}_pool_peak_mb", num(peak, 0))]
    if exceptions:
        lines += ["", "Refusals under the fair pool that are not above the quota the wrapper "
                  "computes, by class. The wrapper computes the quota after the pool has decided, "
                  "from its own count of the consumers registered then: a request just under it "
                  "can be one that was above the quota the pool used.", "",
                  head + " class | where | refusals | held plus asked over the quota, median |",
                  "|" + "---|" * 9]
        for k, groups in exceptions.items():
            for (cls, where), ratios in sorted(groups.items()):
                lines.append(f"| {label(k)} | {cls} | {where} | {len(ratios)} | "
                             f"{num(med_of(ratios), 3)} |")
    lines += ["", "By class of consumer (medians over the traced runs; classes that were refused "
              "or reserved at least 1 MB). The peak of a class is the largest sum of what its "
              "consumers held at one moment, as the wrapper kept it; it is not the sum of their "
              "separate peaks, and it is a reservation, not resident memory. A row reads the "
              "completed traced runs of its configuration, or the failed ones when none "
              "completed, and says which. "
              "The releases after a refusal are what the wrapper sees; "
              "the spills are what the operator of that class reports in its own metrics. The two "
              "are set side by side by class and run: a release is consistent with a spill, it is "
              "not observed as one.", "",
              head + " runs read | class | consumers | peak of the class, MB | largest single, MB | "
              "refusals | releases after a refusal | spills the operator reports | "
              "consumers ending refused |", "|" + "---|" * 14]
    for k in keys:
        ts = traces(k)
        done = [(r, t) for r, t in ts if str(r["ok"]) == "1"]
        # the completed runs when there are any, so that the wrapper's columns and the
        # operator's spills are of the same runs; otherwise the failed ones, without spills
        used = done or ts
        names = sorted({n for _, t in used for n, c in t["classes"].items()
                        if c["refusals"] or c["class_peak"] >= mb})
        for name in names:
            def m(field, scale=1):          # a run without the class counts as zero
                return med_of([t["classes"].get(name, {}).get(field, 0) / scale for _, t in used])
            col = SPILLS_OF.get(name)
            spills = (num(median([r for r, _ in done], col), 0) if col and done
                      else "no completed run" if col else "-")
            lines.append(f"| {label(k)} | {'completed' if done else 'failed'}, {len(used)} | {name} | "
                         f"{num(m('consumers'), 0)} | "
                         f"{num(m('class_peak', mb), 1)} | {num(m('largest_peak', mb), 1)} | "
                         f"{num(m('refusals'), 0)} | {num(m('then_release'), 0)} | {spills} | "
                         f"{num(m('ending_refused'), 0)} |")
            if "Aggregate" in name or name in ("ExternalSorter", "RepartitionExec",
                                               "RepartitionExec[Merge]"):
                figures.append((f"trace_{'_'.join(k)}_{name}_class_peak_mb",
                                num(m("class_peak", mb), 1)))
    finals = ("FinalHashAggregateStream", "OrderedFinalAggregateStream")
    lines += ["", "The final aggregate at its refusals, under the fair pool: on the refusals kept, "
              "over all the traced runs, how many consumers that can spill were registered, how "
              "much those that cannot spill held, and the quota the wrapper computes; and the "
              "peak of the class in each run. The quota is the limit less the second, divided by "
              "the first: few consumers and a small quota mean that the second is what narrows it.", "",
              head + " refusals kept | consumers that can spill | held by those that cannot, MB | "
              "quota, MB | peak of the class by run, MB |", "|" + "---|" * 10]
    for k in keys:
        if k[4] != "fair":
            continue
        ts = traces(k)
        ev = [e for _, t in ts for e in t["events"]
              if e["kind"] == "refusal" and class_name(e["consumer"]) in finals]
        peaks = sorted(t["classes"][n]["class_peak"] / mb for _, t in ts
                       for n in finals if n in t["classes"])
        if not ev:
            continue
        quotas = [e["quota"] / mb for e in ev if e["quota"] is not None]
        spill = f"{min(e['spillable'] for e in ev)} to {max(e['spillable'] for e in ev)}"
        held = f"{min(e['unspillable'] for e in ev) / mb:.0f} to {max(e['unspillable'] for e in ev) / mb:.0f}"
        quota = f"{min(quotas):.1f} to {max(quotas):.1f}" if quotas else "n/a"
        by_run = ", ".join(f"{x:.1f}" for x in peaks)
        lines.append(f"| {label(k)} | {len(ev)} | {spill} | {held} | {quota} | {by_run} |")
        tag = "trace_" + "_".join(k) + "_final_aggregate"
        figures += [(f"{tag}_refusals_kept", str(len(ev))), (f"{tag}_spillable_registered", spill),
                    (f"{tag}_unspillable_held_mb", held), (f"{tag}_quota_mb", quota),
                    (f"{tag}_class_peak_by_run_mb", by_run)]
    lines += ["", "Runs that fail. The error the query returns names the consumer whose request "
              "ended it; a consumer that merely ends with a refused request can have been cancelled "
              "after that. For the traced runs that failed: the class the error names, and the "
              "last refusal kept for the consumer it names (medians over those runs).", "",
              head + " failed, plain / traced | class named by the error | asked, MB | held, MB | "
              "quota, MB | pool reserved, MB | limit, MB | holders at that moment (first run) |",
              "|" + "---|" * 13]
    for k in keys:
        p, t = sel(k, 0), sel(k, 1)
        failed = [(r, tr) for r, tr in traces(k) if str(r["ok"]) != "1"]
        t_failed = len(t) - len(completed(t))
        if len(completed(p)) == len(p) and not t_failed:
            continue
        if not failed:
            why = ("no traced run failed" if not t_failed else
                   "the traced runs that failed left no trace")
            lines.append(f"| {label(k)} | {len(p) - len(completed(p))} of {len(p)} / "
                         f"{t_failed} of {len(t)} | {why} | - | - | - | - | - | - |")
            continue
        named, last, holders = [], [], ""
        for r, tr in failed:
            who = r["error"].partition("failed for ")[2].partition(" with top")[0]
            named.append(class_name(who) if who else "not named")
            hits = [e for e in tr["events"] if e["kind"] == "refusal" and e["consumer"] == who]
            if hits:
                last.append((hits[-1], tr["pool"]["limit"]))
                holders = holders or "; ".join(
                    f"{x.split('=')[0]} {int(x.split('=')[1]) / mb:.0f}"
                    for x in hits[-1]["top"].split(";") if x)
        classes = ", ".join(f"{c} ({named.count(c)})" for c in sorted(set(named))) or "n/a"

        def lm(f):
            return num(med_of([f(e, lim) for e, lim in last]), 1) if last else "not kept"

        if len(failed) < t_failed:
            classes += f"; {t_failed - len(failed)} failed without a trace"
        lines.append(f"| {label(k)} | {len(p) - len(completed(p))} of {len(p)} / "
                     f"{t_failed} of {len(t)} | {classes} | "
                     f"{lm(lambda e, lim: e['bytes'] / mb)} | {lm(lambda e, lim: e['held'] / mb)} | "
                     f"{lm(lambda e, lim: e['quota'] / mb if e['quota'] is not None else None)} | "
                     f"{lm(lambda e, lim: e['reserved'] / mb)} | "
                     f"{lm(lambda e, lim: lim / mb if lim else None)} | {holders or 'n/a'} |")
        tag = f"trace_{'_'.join(k)}_error"
        figures += [(f"{tag}_class", classes),
                    (f"{tag}_asked_mb", lm(lambda e, lim: e["bytes"] / mb)),
                    (f"{tag}_held_mb", lm(lambda e, lim: e["held"] / mb)),
                    (f"{tag}_quota_mb",
                     lm(lambda e, lim: e["quota"] / mb if e["quota"] is not None else None)),
                    (f"{tag}_pool_reserved_mb", lm(lambda e, lim: e["reserved"] / mb)),
                    (f"{tag}_holders", holders or "n/a")]
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- string views

def string_views(out, figures):
    path = Path(out) / "results.tsv"
    if not path.is_file() or not path.stat().st_size:
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
    if not path.is_file() or not path.stat().st_size:
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
    if not path.is_file() or not path.stat().st_size:
        return missing("Depth 1 against depth 2: backpressure", out)
    rows = read_tsv(path)
    sizes = sorted({int(r["batch_size"]) for r in rows if r["kind"] == "batch"})
    per_config = max(len(select(rows, name=n)) for n in {r["name"] for r in rows})
    lines = ["### Depth 1 against depth 2: backpressure", "",
             "`experiments/depth/run.py`: the deduplication with two ordered groups whose files "
             "do not overlap (depth 1) or overlap each with the next (depth 2), same plan in both, 256 MB, "
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
              f"(the `send_time` metric, summed over the inputs and over the repartitions of the plan) is {num(send[default][0], 2)} s at depth 1 "
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
    if not path.is_file() or not path.stat().st_size:
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
    if not path.is_file() or not path.stat().st_size:
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
             "| query | plan | completed | file groups | peak RSS, MB | the peak falls at this share of the run | "
             "Parquet files open at the peak | most Parquet files open at once | RSS then, MB | "
             "most temporary files open | most descriptors |", "|" + "---|" * 11]
    seen = {}
    for query, qlabel in queries.items():
        for variant, plabel in plans.items():
            sel = select(rows, query=query, variant=variant)
            ok = completed(sel)
            v = seen[query, variant] = {k: median(ok, k) for k in keys}
            groups = sorted({int(g) for g in ((r.get("scan_groups") or "") for r in ok) if g.isdigit()})
            lines.append(f"| {qlabel} | {plabel} | {n_of(sel)} | "
                         f"{', '.join(map(str, groups)) or 'n/a'} | "
                         f"{num(v['peak_rss_mb'], 0)} | "
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
    if not path.is_file() or not path.stat().st_size:
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
             "| case | plan | completed as set | completed without | peak RSS as set, MB | peak RSS without huge pages, MB | "
             "elapsed as set, s | elapsed without, s | minor faults as set | without |",
             "|" + "---|" * 10]
    med = {}
    for name in names:
        sel = select(rows, name=name)
        arm_on, arm_off = select(sel, huge_pages="as set"), select(sel, huge_pages="off")
        on, off = completed(arm_on), completed(arm_off)
        med[name] = {"on": median(on, "rss_mb"), "off": median(off, "rss_mb"),
                     "t_on": median(on, "elapsed_s"), "t_off": median(off, "elapsed_s")}
        lines.append(f"| {sel[0]['case']} | {plan[sel[0]['variant']]} | {n_of(arm_on)} | {n_of(arm_off)} | "
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
    known = not isnan(faults_on) and not isnan(faults_off)          # both arms completed
    in_use = known and faults_off > 2 * faults_on
    lines += ["", ("- One arm of the ordered plan at 1200 files has no completed run: whether huge "
                   "pages were in use as the machine is set cannot be told." if not known else
                   f"- As the machine is set the ordered plan at 1200 files takes {num(faults_on, 0)} minor "
                   f"page faults, without huge pages {num(faults_off, 0)}: huge pages are in use in the "
                   f"first arm." if in_use else
                   f"- Without huge pages the ordered plan at 1200 files does not take more than twice "
                   f"the minor page faults ({num(faults_on, 0)} as set, {num(faults_off, 0)} "
                   f"without): huge pages were NOT in use as the machine is set, and the "
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
