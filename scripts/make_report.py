"""Write RESULTS.md from results/: tables, findings and checks, all computed.

Run from the repository root (scripts/run_lab.sh does it as its last step):
  python scripts/make_report.py [--results DIR]

Nothing in the report is written by hand. The tables come from the recorded
runs; the findings are sentences whose numbers and whose qualitative words
("faster", "within the quartiles") are computed from them; the predictions of
DESIGN.md are checked by rules stated next to each one. Also writes
results/figures.tsv, the figures the companion article quotes.
"""
import argparse
import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "experiments"))
import report as experiments  # noqa: E402

VARIANT_ORDER = ["original", "accept-groups", "original-target", "original-split-off"]
AXES = [
    ("base", "The base case", "12 files, depth 4, shape S1, 256 MB, Q3, four variants."),
    ("A1", "A1: the consumer of the order", "Base data and pool; the query changes."),
    ("A2", "A2: the overlap depth", "Base data at depth 1, 2 and 12 (depth 4 is the base case)."),
    ("A3", "A3: the memory budget", "Base data and query at 128 and 512 MB (256 MB is the base case)."),
    ("A4", "A4 and A4 x A3: the width of the strings", "Shapes S0 and S2 at the three pool sizes; S1 at the three pool sizes is the base case and A3."),
    ("A5", "A5 and A5 x A3: the number of files for the same rows", "120 and 1200 files, with depth 4 (one entity per file) and with depth equal to the files (total overlap); the 12-file points are the base case and A2 at depth 12."),
    ("A6", "A6: the share of duplicates", "Base data with half the rows copied."),
]
NOT_MEASURED = """\
- What the order-preserving repartition holds for each pair of input and
  output partition: the per-pair cost is a coefficient derived from the growth
  of the RSS with the outputs, not traced in the code.
- The work of the merge itself, the comparisons among the heads of the
  streams, apart from the memory and the waiting it brings.
- The partial aggregates' share of the many-stream memory: the query with a
  small aggregate state also reads fewer columns.
- What `output_bytes` measures beyond its definition (the cumulative bytes of
  the batches an operator emitted, as Arrow accounts for their buffers): it is
  not peak resident memory and not necessarily unique bytes.
- How much of the final-aggregate spills on the wide strings comes from the
  repartition's reservations and how much from other consumers of the fair
  pool. No run changed the accounting itself or instrumented the reservations.
- A pool at which the original plan does not spill, the greedy pool, and
  larger data: every query here runs under a second on a few MB of Parquet.
- The per-file overhead of small files is inside the elapsed time and the
  `CREATE` time, not broken down into opens, footer reads and metadata.
- The `GROUP BY` on the whole sort key at a size where the hash aggregate
  would spill.
- Which descriptors the ordered plan holds when it meets the open-file limit."""


def read_tsv(path):
    with Path(path).open() as f:
        return list(csv.DictReader(f, delimiter="\t"))


def num(x, digits=3):
    return f"{x:.{digits}f}"


class Matrix:
    """The summary of the matrix, addressed by case, pool and variant."""

    def __init__(self, summary):
        self.rows = summary
        self.by = {(r["case"], r["pool"], r["variant"]): r for r in summary}

    def has(self, case, pool, variant):
        return (case, pool, variant) in self.by

    def get(self, case, pool, variant, key):
        value = self.by[case, pool, variant][key]
        return float(value) if value != "" else float("nan")

    def med(self, case, pool, variant):
        return self.get(case, pool, variant, "elapsed_med")

    def rss(self, case, pool, variant):
        return self.get(case, pool, variant, "rss_mb_med")

    def spills(self, case, pool, variant, operator):
        return self.get(case, pool, variant, f"{operator}_spills")

    def ok(self, case, pool, variant):
        return self.by[case, pool, variant]["ok"]

    def gain(self, case, pool, variant="accept-groups", against="original"):
        """Percent by which `variant` is faster than `against` (medians)."""
        return 100 * (1 - self.med(case, pool, variant) / self.med(case, pool, against))

    def compare(self, a, b):
        """How `a` stands to `b`, both (case, pool, variant): by quartiles."""
        if self.get(*a, "elapsed_q3") < self.get(*b, "elapsed_q1"):
            return "faster"
        if self.get(*a, "elapsed_q1") > self.get(*b, "elapsed_q3"):
            return "slower"
        return "within the quartiles"

    def versus(self, a, b, name):
        """'faster than <name>', 'slower than <name>' or 'within the quartiles of <name>'."""
        word = self.compare(a, b)
        return f"{word} of {name}" if word.startswith("within") else f"{word} than {name}"

    def pair(self, case, pool, variant="accept-groups", against="original"):
        """'0.377 against 0.660 s' for a variant and its reference."""
        return f"{num(self.med(case, pool, variant))} against {num(self.med(case, pool, against))} s"


# ------------------------------------------------------------------ tables

def rows_of(summary, axis):
    sel = [r for r in summary if r["axis"] == axis]
    if axis != "base":
        sel = [r for r in summary if r["case"] == "base" and r["variant"] != "original-split-off"] + sel
    return sorted(sel, key=lambda r: (r["case"] != "base", r["case"], int(r["pool"][:-1]),
                                       VARIANT_ORDER.index(r["variant"])))


def elapsed(r):
    done, total = r["ok"].split("/")
    if done == "0":
        return f"fails ({r['ok']})"
    note = "" if done == total else f" ({r['ok']})"
    return f"{r['elapsed_med']} [{r['elapsed_q1']}..{r['elapsed_q3']}]{note}"


def spill(r, key):
    c, mb = r[f"{key}_spills"], r[f"{key}_spill_mb"]
    if c == "":
        return "-"
    return "0" if float(c) == 0 else f"{float(c):g} / {mb} MB"


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


def manifest_meta(path):
    meta, body = {}, []
    for line in Path(path).read_text().splitlines():
        if line.startswith("# "):
            parts = line[2:].split("\t")
            meta[parts[0]] = parts[1]
        elif line and line.split("\t")[0].isdigit():
            body.append(line.split("\t"))
    return meta, body


def datasets(manifests):
    lines = ["| dataset | files | depth | assignment | shape | rows | distinct keys | duplicate rows | groups by bounds | bytes | row groups |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for path in sorted(Path(manifests).glob("*.tsv")):
        meta, body = manifest_meta(path)
        size = sum(int(b[2]) for b in body)
        rgs = sum(int(b[3]) for b in body)
        lines.append(f'| {meta["dataset"]} | {meta["files"]} | {meta["depth"]} | {meta["assign"]} '
                     f'| {meta["shape"]} | {meta["rows"]} | {meta["distinct_grouping_keys"]} '
                     f'| {int(meta["rows"]) - int(meta["distinct_grouping_keys"]):,} | {meta["groups_by_bounds"]} | {size:,} | {rgs} |')
    return "\n".join(lines)


def plan_table(rows):
    lines = ["| case | variant | query | groups | SortExec | preserve_order | partial mode | final mode | output_ordering | check |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f'| {r["case"]} | {r["variant"]} | {r["query"]} | {r["scan_groups"]} | {r["sort_exec"]} '
                     f'| {r["preserve_order"]} | {r["partial_mode"] or "-"} | {r["final_mode"] or "-"} '
                     f'| {r["output_ordering"]} | {r["check"]} |')
    return "\n".join(lines)


# ------------------------------------------------------------------ findings

def findings(m, manifests, figures):
    """Numbered sentences on the matrix; every number and comparison is computed."""
    B, P = "base", "256m"
    O, A, T = (B, P, "original"), (B, P, "accept-groups"), (B, P, "original-target")
    out = []

    def spilled(case, pool, variant, operator):
        return f"{m.spills(case, pool, variant, operator):g}"

    # 1 base
    text = (f"Base case, 256 MB, deduplication. The original plan takes {num(m.med(*O))} s, with "
            f"{spilled(*O, 'sort')} spills in the sort and {spilled(*O, 'final_agg')} in the final "
            f"aggregate. The ordered plan takes {num(m.med(*A))} s, {m.gain(B, P):.0f} percent less, "
            f"with {spilled(*A, 'final_agg')} spills in the final aggregate and "
            f"{spilled(*A, 'repartition')} in the repartition, and {m.rss(*O) - m.rss(*A):.0f} MB less "
            f"RSS ({m.rss(*A):.0f} against {m.rss(*O):.0f}). ")
    text += (f"Raising `target_partitions` to the groups needed (`original-target`) takes "
             f"{num(m.med(*T))} s, {m.versus(T, A, 'the ordered plan')}, with {m.rss(*T):.0f} MB of RSS. ")
    if m.has(B, P, "original-split-off"):
        S = (B, P, "original-split-off")
        text += (f"With the grouping by statistics off the time is {num(m.med(*S))} s, "
                 f"{m.versus(S, O, 'the original')}.")
    out.append(text)
    figures += [("base_original_s", num(m.med(*O))), ("base_ordered_s", num(m.med(*A))),
                ("base_gain_pct", f"{m.gain(B, P):.0f}"), ("base_original_rss_mb", f"{m.rss(*O):.0f}"),
                ("base_ordered_rss_mb", f"{m.rss(*A):.0f}"), ("base_target_s", num(m.med(*T))),
                ("base_target_rss_mb", f"{m.rss(*T):.0f}")]

    # 2 consumers
    parts = []
    names = {"A1-Q1": "`ORDER BY` only (Q1)", "A1-Q2": "`GROUP BY` on the whole sort key (Q2)",
             "A1-Q4": "`GROUP BY` without the sort key (Q4)"}
    for case, label in names.items():
        o, a = (case, P, "original"), (case, P, "accept-groups")
        parts.append(f"{label}: {m.pair(case, P)}, the ordered plan {m.compare(a, o)}, RSS "
                     f"{m.rss(*a):.0f} against {m.rss(*o):.0f} MB")
        figures += [(f"{case}_original_s", num(m.med(*o))), (f"{case}_ordered_s", num(m.med(*a))),
                    (f"{case}_original_rss_mb", f"{m.rss(*o):.0f}"), (f"{case}_ordered_rss_mb", f"{m.rss(*a):.0f}")]
    out.append("A1, who uses the order. " + "; ".join(parts) + f"; the deduplication (Q3) is the base "
               f"case, {m.pair(B, P)}. In Q3 two things change together, a sort disappears and the "
               f"aggregate can close completed prefixes early; the comparison measures their "
               f"combined effect.")

    # 3 depth
    parts = []
    for d in (1, 2):
        case = f"A2-depth-{d}"
        parts.append(f"depth {d}: {m.pair(case, P)}, {m.compare((case, P, 'accept-groups'), (case, P, 'original'))}")
    c12 = "A2-depth-12"
    text = ("A2, the overlap depth. At depth 1 and 2 both binaries produce the same plan; "
            + "; ".join(parts) + f". At depth 12, twelve ordered groups: {m.pair(c12, P)}, a "
            f"{m.gain(c12, P):.0f} percent reduction against {m.gain(B, P):.0f} at depth 4, with "
            f"{spilled(c12, P, 'accept-groups', 'repartition')} spills in the order-preserving "
            f"repartition and {m.rss(c12, P, 'accept-groups'):.0f} MB of RSS against "
            f"{m.rss(c12, P, 'original'):.0f} for the original.")
    if m.has(c12, P, "original-target"):
        t = (c12, P, "original-target")
        text += (f" `original-target` with twelve partitions takes {num(m.med(*t))} s, "
                 f"{m.versus(t, (c12, P, 'accept-groups'), 'the ordered plan')}, with "
                 f"{spilled(*t, 'final_agg')} spills in the final aggregate and {m.rss(*t):.0f} MB of RSS.")
    out.append(text)
    d1, d2 = m.med("A2-depth-1", P, "original"), m.med("A2-depth-2", P, "original")
    figures += [("depth12_original_s", num(m.med(c12, P, "original"))),
                ("depth12_ordered_s", num(m.med(c12, P, "accept-groups"))),
                ("depth12_gain_pct", f"{m.gain(c12, P):.0f}"),
                ("depth12_original_rss_mb", f"{m.rss(c12, P, 'original'):.0f}"),
                ("depth12_ordered_rss_mb", f"{m.rss(c12, P, 'accept-groups'):.0f}"),
                ("depth12_ordered_repartition_spills", spilled(c12, P, "accept-groups", "repartition")),
                ("depth1_original_s", num(d1)), ("depth2_original_s", num(d2))]

    # 4 pools
    pools = [("A3", "128m"), (B, "256m"), ("A3", "512m")]
    gains = [m.gain(c, p) for c, p in pools]
    orig_spills = [m.spills(c, p, "original", "final_agg") for c, p in pools]
    ord_spills = [m.spills(c, p, "accept-groups", "final_agg") for c, p in pools]
    text = ("A3, the memory budget. Gain of the ordered plan at 128, 256 and 512 MB: "
            + ", ".join(f"{g:.0f}" for g in gains) + " percent. The final aggregate of the original "
            "plan spills " + ", ".join(f"{s:g}" for s in orig_spills) + " times, that of the ordered "
            "plan " + ", ".join(f"{s:g}" for s in ord_spills) + ". ")
    if all(s > 0 for s in orig_spills) and all(s == 0 for s in ord_spills):
        text += ("One plan spills there at every pool and the other at none, so this series does "
                 "not separate the benefit of early emission from that of the spills avoided.")
    else:
        text += "The two plans differ in whether they spill at some pools and not at others."
    out.append(text)
    figures += [("gain_pct_128_256_512", " / ".join(f"{g:.0f}" for g in gains))]
    for (c, p) in pools:
        figures += [(f"budget_{p}_original_rss_mb", f"{m.rss(c, p, 'original'):.0f}"),
                    (f"budget_{p}_ordered_rss_mb", f"{m.rss(c, p, 'accept-groups'):.0f}"),
                    (f"budget_{p}_original_sort_spills", spilled(c, p, "original", "sort"))]

    # 5 strings
    shapes = {"S0": "A4-S0", "S1": None, "S2": "A4-S2"}
    def case_of(shape, pool):
        if shape == "S1":
            return B if pool == "256m" else "A3"
        return shapes[shape]
    rows = []
    for shape in ("S0", "S1", "S2"):
        rows.append(f"{shape}: " + ", ".join(f"{m.gain(case_of(shape, p), p):.0f}" for p in ("128m", "256m", "512m")))
    s2 = ("A4-S2", "128m", "accept-groups")
    out.append("A4 x A3, the width of the strings. Gain of the ordered plan at 128, 256 and 512 MB, "
               "percent: " + "; ".join(rows) + f". On the wide strings (S2) at 128 MB the final "
               f"aggregate of the ordered plan spills {spilled(*s2, 'final_agg')} times and the "
               f"plan takes {m.pair('A4-S2', '128m')} ({m.ok('A4-S2', '128m', 'original')} runs of "
               f"the original completed). At 256 MB the order-preserving repartition reports "
               f"{m.get('A4-S0', P, 'accept-groups', 'repartition_out_mb'):.0f}, "
               f"{m.get(B, P, 'accept-groups', 'repartition_out_mb'):.0f} and "
               f"{m.get('A4-S2', P, 'accept-groups', 'repartition_out_mb'):.0f} MB of output for S0, S1 "
               f"and S2, the scan {m.get('A4-S0', P, 'accept-groups', 'scan_out_mb'):.0f}, "
               f"{m.get(B, P, 'accept-groups', 'scan_out_mb'):.0f} and "
               f"{m.get('A4-S2', P, 'accept-groups', 'scan_out_mb'):.0f}. The experiment on string "
               f"views below changes the representation alone.")
    figures += [("matrix_gain_S2_128_pct", f"{m.gain('A4-S2', '128m'):.0f}"),
                ("matrix_gain_S0_128_pct", f"{m.gain('A4-S0', '128m'):.0f}")]

    # 6 files
    few = [(B, "12"), ("A5-120-depth-4", "120"), ("A5-1200-depth-4", "1200")]
    meta = {}
    for path in Path(manifests).glob("*.tsv"):
        mm, _ = manifest_meta(path)
        meta[mm["dataset"]] = mm
    g1200 = meta.get("df-16919-partial-1200-depth-4-entity-rank", {}).get("groups_by_bounds", "?")
    text = ("A5, the number of files. With depth 4, at 12, 120 and 1200 files the original takes "
            + ", ".join(num(m.med(c, P, "original")) for c, _ in few) + " s and the ordered plan "
            + ", ".join(num(m.med(c, P, "accept-groups")) for c, _ in few) + f" s; at 1200 files the "
            f"bounds give {g1200} groups. ")
    many = "A5-1200-depth-1200"
    c120 = "A5-120-depth-120"
    text += (f"With total overlap, 120 groups at 256 MB: {m.pair(c120, P)}, "
             f"{m.compare((c120, P, 'accept-groups'), (c120, P, 'original'))}, with "
             f"{spilled(c120, P, 'accept-groups', 'repartition')} spills in the repartition and "
             f"{m.rss(c120, P, 'accept-groups'):.0f} MB of RSS against {m.rss(c120, P, 'original'):.0f}. "
             f"With {m.by[many, P, 'accept-groups']['groups']} groups: ")
    parts, rss_a, rss_o = [], [], []
    for pool in ("128m", "256m", "512m"):
        a, o = (many, pool, "accept-groups"), (many, pool, "original")
        parts.append(f"{pool[:-1]} MB {m.pair(many, pool)}, {m.compare(a, o)}, "
                     f"{spilled(*a, 'final_agg')} spills in the ordered final aggregate")
        rss_a.append(m.rss(*a))
        rss_o.append(m.rss(*o))
        figures += [(f"many_groups_{pool}_original_s", num(m.med(*o))), (f"many_groups_{pool}_ordered_s", num(m.med(*a))),
                    (f"many_groups_{pool}_ordered_final_spills", spilled(*a, "final_agg")),
                    (f"many_groups_{pool}_ordered_rss_mb", f"{m.rss(*a):.0f}")]
    text += ("; ".join(parts) + f"; RSS {min(rss_a) / 1024:.1f} to {max(rss_a) / 1024:.1f} GB against "
             f"{min(rss_o) / 1024:.2f} to {max(rss_o) / 1024:.2f}. The two 1200-file layouts differ in the "
             f"streams and also in how the rows are assigned to the files.")
    out.append(text)
    figures += [("files_1200_depth4_original_s", num(m.med("A5-1200-depth-4", P, "original"))),
                ("files_1200_depth4_ordered_s", num(m.med("A5-1200-depth-4", P, "accept-groups"))),
                ("files_1200_depth4_ordered_rss_mb", f"{m.rss('A5-1200-depth-4', P, 'accept-groups'):.0f}"),
                ("files_1200_depth4_groups", str(g1200)),
                ("many_groups_ordered_rss_gb", f"{min(rss_a) / 1024:.1f} to {max(rss_a) / 1024:.1f}"),
                ("many_groups_original_rss_gb", f"{min(rss_o) / 1024:.2f} to {max(rss_o) / 1024:.2f}")]

    # 7 duplicates
    dup = "A6-dup-0.5"
    drop_o = 100 * (1 - m.med(dup, P, "original") / m.med(*O))
    drop_a = 100 * (1 - m.med(dup, P, "accept-groups") / m.med(*A))
    out.append(f"A6, half the rows duplicated: {m.pair(dup, P)}, a gain of {m.gain(dup, P):.0f} percent "
               f"against {m.gain(B, P):.0f} without duplicates. The time of the original falls by "
               f"{drop_o:.0f} percent, that of the ordered plan by {drop_a:.0f}.")
    figures += [("dup_original_s", num(m.med(dup, P, "original"))), ("dup_ordered_s", num(m.med(dup, P, "accept-groups"))),
                ("dup_gain_pct", f"{m.gain(dup, P):.0f}")]

    # 8 workaround
    verdicts = {"faster": [], "within the quartiles": [], "slower": []}
    more_rss = 0
    cells = [(c, p) for (c, p, v) in m.by if v == "original-target" and m.by[c, p, v]["query"] == "Q3"]
    for c, p in sorted(cells, key=lambda x: (x[0], int(x[1][:-1]))):
        t, a = (c, p, "original-target"), (c, p, "accept-groups")
        verdicts[m.compare(t, a)].append(f"{c} at {p[:-1]} MB ({m.pair(c, p, 'original-target', 'accept-groups')})")
        more_rss += m.rss(*t) > m.rss(*a)
    text = "The workaround, `original-target`, against the ordered plan on the deduplication. "
    for word, label in (("faster", "Faster"), ("within the quartiles", "Within the quartiles"), ("slower", "Slower")):
        if verdicts[word]:
            text += f"{label}: " + "; ".join(verdicts[word]) + ". "
    text += f"It has more RSS in {more_rss} of {len(cells)} cases."
    out.append(text)

    # 9 depth 1 against 2
    o1, o2 = ("A2-depth-1", P, "original"), ("A2-depth-2", P, "original")
    out.append(f"Depth 1 against depth 2, same plan, unmodified binary: {num(d1)} against {num(d2)} s, "
               f"depth 1 {m.compare(o1, o2)}. The experiment on depth below varies the batch size.")
    return "\n".join(f"{i}. {t}" for i, t in enumerate(out, 1))


def predictions(m):
    """The hypotheses of DESIGN.md, each with the rule that checks it and its outcome."""
    B, P = "base", "256m"
    rows = []

    def add(name, prediction, rule, value, holds):
        outcome = {True: "holds", False: "does not hold", None: "reported"}[holds]
        rows.append(f"| {name} | {prediction} | {rule} | {value} | {outcome} |")

    g = {q: m.gain(c, P) for q, c in (("Q1", "A1-Q1"), ("Q2", "A1-Q2"), ("Q3", B), ("Q4", "A1-Q4"))}
    add("H1", "the order buys the most for Q2 and Q3", "gain of Q2 and of Q3 above that of Q1",
        ", ".join(f"{q} {v:.0f} %" for q, v in g.items()), g["Q2"] > g["Q1"] and g["Q3"] > g["Q1"])
    q4 = m.compare(("A1-Q4", P, "accept-groups"), ("A1-Q4", P, "original"))
    add("H1", "Q4: whatever differs is described", "quartiles of the two variants", q4, None)
    same = [m.compare((f"A2-depth-{d}", P, "accept-groups"), (f"A2-depth-{d}", P, "original")) for d in (1, 2)]
    add("H2", "at depth 1 and 2 the variants are identical", "both within the quartiles",
        "; ".join(same), all(s == "within the quartiles" for s in same))
    g4, g12 = m.gain(B, P), m.gain("A2-depth-12", P)
    add("H2", "the gain shrinks at depth 12", "gain at depth 12 below gain at depth 4",
        f"{g12:.0f} % against {g4:.0f} %", g12 < g4)
    gp = [m.gain("A3", "128m"), m.gain(B, P), m.gain("A3", "512m")]
    add("H3", "the gain grows with the pool", "gain at 128 < 256 < 512 MB",
        " / ".join(f"{x:.0f} %" for x in gp), gp[0] < gp[1] < gp[2])
    w = m.compare(("A3", "128m", "accept-groups"), ("A3", "128m", "original"))
    add("H3", "at 128 MB the ordered plan still wins", "ordered faster beyond the quartiles", w, w == "faster")
    s2 = m.spills("A4-S2", "128m", "accept-groups", "final_agg")
    gs = (m.gain("A4-S2", "128m"), m.gain("A3", "128m"))
    add("H4", "wide strings bring spills to the ordered plan at 128 MB and shrink its gain",
        "final-aggregate spills above zero and gain below that of S1",
        f"{s2:g} spills; {gs[0]:.0f} % against {gs[1]:.0f} %", s2 > 0 and gs[0] < gs[1])
    r512 = m.compare(("A4-S2", "512m", "accept-groups"), ("A4-S2", "512m", "original"))
    add("H4", "at 512 MB the ranking does not change", "ordered faster beyond the quartiles on S2", r512, r512 == "faster")
    gap12 = m.med(B, P, "original") - m.med(B, P, "accept-groups")
    gap1200 = m.med("A5-1200-depth-4", P, "original") - m.med("A5-1200-depth-4", P, "accept-groups")
    add("H5", "at depth 4 the gap between the variants stays from 12 to 1200 files",
        "gap at 1200 files within 25 % of the gap at 12", f"{gap1200:.3f} s against {gap12:.3f} s",
        abs(gap1200 - gap12) / gap12 < 0.25)
    many = "A5-1200-depth-1200"
    lo = m.compare((many, "128m", "accept-groups"), (many, "128m", "original"))
    hi = m.compare((many, "512m", "accept-groups"), (many, "512m", "original"))
    add("H5", "about 1200 groups lose at 128 MB and not at 512 MB", "slower at 128, not slower at 512",
        f"128 MB: {lo}; 512 MB: {hi}", lo == "slower" and hi != "slower")
    dup = "A6-dup-0.5"
    add("H6", "no prediction on which plan profits more from duplicates", "gain with and without duplicates",
        f"{m.gain(dup, P):.0f} % against {m.gain(B, P):.0f} %", None)
    t = m.compare((B, P, "original-target"), (B, P, "accept-groups"))
    add("H7", "the workaround's time is not predicted to equal the ordered plan's", "quartiles at the base case", t, None)
    return "\n".join(["| hypothesis | prediction | rule | value | outcome |", "|---|---|---|---|---|"] + rows)


# ------------------------------------------------------------------ report

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--results", type=Path, default=ROOT / "results")
    args = p.parse_args()
    res, prov = args.results, ROOT / "provenance"
    matrix, manifests = res / "matrix", res / "manifests"
    summary = read_tsv(matrix / "summary.tsv")
    m = Matrix(summary)
    machine = dict(l.split("\t", 1) for l in (matrix / "machine.txt").read_text().splitlines() if "\t" in l)
    runs = read_tsv(matrix / "results.tsv")
    plans = read_tsv(matrix / "plan-check.tsv")
    checks = read_tsv(res / "result-check" / "result-check.tsv") if (res / "result-check" / "result-check.tsv").is_file() else []
    recorded = (prov / "binaries.sha256").read_text().strip()
    measured = (matrix / "machine.txt").read_text().split("binaries\n", 1)[1].strip()
    same = {l.split()[0] for l in recorded.splitlines()} == {l.split()[0] for l in measured.splitlines()}
    figures = []

    sections = [f"### {title}\n\n{intro}\n\n{table(summary, axis, with_bytes=(axis == 'A4'))}"
                for axis, title, intro in AXES]
    failed_runs = [r for r in runs if r["result"] != "ok"]
    plan_ok = sum(r["check"] == "ok" for r in plans)
    check_ok = sum(r["check"] == "ok" for r in checks)
    exp = res / "experiments"
    body = f"""# Results

Generated by `scripts/make_report.py` from `results/`, the outputs of one run of
`scripts/run_lab.sh`. Nothing below is written by hand: the tables are the
recorded runs, and the numbers and comparisons in the findings are computed
from them. The design and the hypotheses are in `DESIGN.md`.

Comparisons use the quartiles of ten runs: a variant is "faster" than another
when its third quartile is below the other's first, "slower" in the opposite
case, "within the quartiles" otherwise. A gain is the reduction of the median.

## Provenance

- DataFusion commit {(prov / "datafusion-commit.txt").read_text().strip()}, built by
  `scripts/build_binaries.sh` on {(prov / "built-at.txt").read_text().strip()} in a detached worktree with
  {(prov / "toolchain.txt").read_text().splitlines()[0]}; `{(prov / "build-command.txt").read_text().strip()}`.
  `original` is the commit as it is, `accept-groups` the commit plus
  `patch/accept-extra-groups.patch`, the only change:

```diff
{(prov / "applied.patch").read_text().strip()}
```

- SHA-256 of the binaries as built (`provenance/binaries.sha256`); the hashes the
  runner recorded when the matrix started {"match" if same else "DO NOT MATCH"}:

```
{recorded}
```

- {machine["cpu"]}, {machine["threads"]} threads, {machine.get("memory_gb", "?")} GB, {machine["kernel"]}, open-file
  limit {machine.get("open_file_limit", "?")}; matrix started {machine["date"]}. `datafusion-cli
  --mem-pool-type fair --memory-limit <pool>`; `target_partitions = 2` except
  for `original-target`, where it equals the ordered groups the overlap needs;
  `split_file_groups_by_statistics = true` except for `original-split-off`.
- Ten recorded runs per case, pool and variant after one unrecorded warm-up,
  variants in rotating order. Times are the `Elapsed` of `EXPLAIN ANALYZE`, which
  starts before the logical plan is built and includes the file listing and the
  statistics reads of the scan; spills and `output_bytes` per operator from the
  plan metrics; RSS from `/usr/bin/time`. Medians with first and third quartile
  over completed runs; the count of completed runs is given when below ten.
  {len(runs)} runs, {len(failed_runs)} failed.
- Every run's SQL, output and stderr: `results/matrix/<case>/<pool>/`.

## Datasets

All from `scripts/datasets.sh`, fixed seeds, fixed time origin; the same
600,000 rows redistributed, except the duplicate dataset, which holds the same
number of rows with half of them copies.

{datasets(manifests)}

## Plans, checked before timing

`EXPLAIN FORMAT INDENT` once per case and variant
(`results/matrix/<case>/plan-check/`), validated for the sort, the advertised
ordering, the scan groups, the aggregate modes and `preserve_order`.
{plan_ok} of {len(plans)} plans as expected.

{plan_table(plans)}

## Rows returned

`scripts/check_results.py` runs every query plainly, for every case, pool and
variant, and compares the rows with those of `original` in the deterministic
columns; it checks that the rows are sorted where the query orders them, that
the deduplication returns as many rows as the dataset has distinct keys, and
that the plan under the statement that writes the rows is the timed one.
{check_ok} of {len(checks)} checks pass (`results/result-check/result-check.tsv`).
{"" if check_ok == len(checks) else chr(10) + chr(10).join("- " + " ".join((r["case"], r["pool"], r["variant"], r["check"])) for r in checks if r["check"] != "ok") + chr(10)}
## Results by axis

Spill cells: count / spilled MB (medians over completed runs). Modes: of the
final aggregate, or partial / final when they differ. "Out MB" columns: the
cumulative `output_bytes` an operator reports in its metrics, the bytes of
the batches it emitted as Arrow accounts for their buffers; not peak resident
memory, and not necessarily unique bytes.

{(chr(10) * 2).join(sections)}

## Findings on the matrix

{findings(m, manifests, figures)}

## The predictions of DESIGN.md

Each prediction is checked by the rule beside it. "Reported" marks the points
on which the design made no prediction.

{predictions(m)}

## Experiments

One-variable tests on the costs the matrix shows, each in
`results/experiments/<name>/`. Their design is in `DESIGN.md`.

{experiments.string_views(exp / "string-views", figures)}
{experiments.many_streams(exp / "many-streams", figures)}
{experiments.depth(exp / "depth", matrix, manifests, figures)}
{experiments.open_files(exp / "open-files", figures)}
## Not measured

{NOT_MEASURED}
"""
    (ROOT / "RESULTS.md").write_text(body)
    with (res / "figures.tsv").open("w") as f:
        f.write("figure\tvalue\n" + "".join(f"{k}\t{v}\n" for k, v in figures))
    print(ROOT / "RESULTS.md")


if __name__ == "__main__":
    main()
