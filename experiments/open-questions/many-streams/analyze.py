"""Analysis of results.tsv for PLAN.md: slopes with 95 percent intervals, E2, E3.

Failed runs are listed and then left out of every statistic below.
"""
import csv
import random
from pathlib import Path
import statistics as st
from math import sqrt

every = [r for r in csv.DictReader(open(Path(__file__).resolve().parent / "results.tsv"), delimiter="\t")]
bad = [r["name"] + "/" + r["run"] for r in every if r["ok"] != "1"]
print("failed runs:", bad or "none")
rows = [r for r in every if r["ok"] == "1"]
# t quantiles 0.975 by degrees of freedom; beyond 30 the normal value
T = dict(zip(range(1, 31), (
    12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
    2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
    2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042)))


def med(sel, k):
    """Median of a column, or nan when no run of the cell completed."""
    values = [float(r[k]) for r in sel]
    return st.median(values) if values else float("nan")


def slope(xs, ys):
    """Least-squares slope alone; None when it is not defined."""
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else None


def ols(xs, ys):
    n = len(xs); mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    res = [y - a - b * x for x, y in zip(xs, ys)]
    s = sqrt(sum(e * e for e in res) / (n - 2))
    se = s / sqrt(sxx)
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = 1 - sum(e * e for e in res) / ss_tot if ss_tot else float("nan")
    return a, b, se * T.get(n - 2, 1.960), r2


print("\nE1: medians per file count (MB). ordered rss / commit | original rss / commit | groups")
for q in ("Q1", "Q2", "Q3"):
    for f in ("150", "300", "600", "1200"):
        o = [r for r in rows if r["experiment"] == "E1" and r["files"] == f and r["query"] == q and r["binary"] == "accept-groups"]
        b = [r for r in rows if r["experiment"] == "E1" and r["files"] == f and r["query"] == q and r["binary"] == "original"]
        if not o or not b:
            print(f"  {q} {f:>5}: no completed run for {'the ordered plan' if not o else 'the original plan'}")
            continue
        print(f"  {q} {f:>5}: {med(o,'rss_mb'):6.0f} / {med(o,'mi_peak_commit_mb'):5.0f}  [rss range {min(int(r['rss_mb']) for r in o)}-{max(int(r['rss_mb']) for r in o)}]"
              f" | {med(b,'rss_mb'):5.0f} / {med(b,'mi_peak_commit_mb'):4.0f} | groups {o[0]['groups']}"
              f" | user {med(o,'user_s'):.2f} sys {med(o,'sys_s'):.2f}")

print("\nE1: excess over the original (ordered run minus median original), regressed on ordered groups")
for metric in ("rss_mb", "mi_peak_commit_mb"):
    for q in ("Q1", "Q2", "Q3"):
        xs, ys = [], []
        for f in ("150", "300", "600", "1200"):
            orig = [r for r in rows if r["experiment"] == "E1" and r["files"] == f and r["query"] == q and r["binary"] == "original"]
            if not orig:
                continue              # no completed baseline: the file count is left out
            base = med(orig, metric)
            for r in rows:
                if r["experiment"] == "E1" and r["files"] == f and r["query"] == q and r["binary"] == "accept-groups":
                    xs.append(int(r["groups"])); ys.append(float(r[metric]) - base)
        if len(set(xs)) < 3:
            print(f"  {metric:18} {q}: too few completed runs for a slope  n={len(xs)}")
            continue
        a, b, ci, r2 = ols(xs, ys)
        print(f"  {metric:18} {q}: slope {b*1024:6.0f} KB/stream  [95% {(b-ci)*1024:6.0f} .. {(b+ci)*1024:6.0f}]  intercept {a:6.0f} MB  R2 {r2:.2f}  n={len(xs)}")

# The interval above takes each baseline, the median of three original runs, as
# exact, although the five differences of a file count share it. The bootstrap
# below resamples the ordered and the original runs of every file count, so the
# uncertainty of the baseline enters the interval. With three baseline runs per
# point it is a rough interval, not a precise one.
print("\nE1: the same slopes, bootstrap over ordered and original runs (10,000 resamples, seed 16919, 2.5 to 97.5 percentiles)")
rng = random.Random(16919)
for metric in ("rss_mb", "mi_peak_commit_mb"):
    for q in ("Q1", "Q2", "Q3"):
        cells = []
        for f in ("150", "300", "600", "1200"):
            sel = [r for r in rows if r["experiment"] == "E1" and r["files"] == f and r["query"] == q]
            o = [(int(r["groups"]), float(r[metric])) for r in sel if r["binary"] == "accept-groups"]
            b = [float(r[metric]) for r in sel if r["binary"] == "original"]
            if o and b:
                cells.append((o, b))
        if len(cells) < 3:
            print(f"  {metric:18} {q}: too few completed runs for a slope")
            continue
        slopes = []
        for _ in range(10000):
            xs, ys = [], []
            for o, b in cells:
                base = st.median(rng.choices(b, k=len(b)))
                for g, v in rng.choices(o, k=len(o)):
                    xs.append(g); ys.append(v - base)
            slopes.append(slope(xs, ys))
        slopes.sort()
        print(f"  {metric:18} {q}: slope [95% {slopes[249]*1024:6.0f} .. {slopes[9749]*1024:6.0f}] KB/stream")

print("\nE2: Q3, 1200 files, outputs = target_partitions")
e2 = {"2": [r for r in rows if r["experiment"] == "E1" and r["files"] == "1200" and r["query"] == "Q3" and r["binary"] == "accept-groups"]}
for t in ("4", "8"):
    e2[t] = [r for r in rows if r["experiment"] == "E2" and r["target"] == t]
for t, sel in e2.items():
    if not sel:
        print(f"  outputs {t}: no completed run")
        continue
    rss = sorted(int(r["rss_mb"]) for r in sel); com = sorted(int(r["mi_peak_commit_mb"]) for r in sel)
    print(f"  outputs {t}: rss median {st.median(rss):.0f} {rss}  commit median {st.median(com):.0f} {com}  elapsed {med(sel,'elapsed_s'):.3f}")

print("\nE3: 1200 files, default purge delay against MIMALLOC_PURGE_DELAY=0")
for q in ("Q1", "Q3"):
    d = [r for r in rows if r["experiment"] == "E1" and r["files"] == "1200" and r["query"] == q and r["binary"] == "accept-groups"]
    z = [r for r in rows if r["experiment"] == "E3" and r["query"] == q]
    for lab, sel in (("default", d), ("purge 0", z)):
        if not sel:
            print(f"  {q} {lab:8}: no completed run")
            continue
        rss = sorted(int(r["rss_mb"]) for r in sel); com = sorted(int(r["mi_peak_commit_mb"]) for r in sel)
        print(f"  {q} {lab:8}: rss {rss} median {st.median(rss):.0f} | commit {com} median {st.median(com):.0f} | elapsed {med(sel,'elapsed_s'):.3f}")
