"""Analysis of results.tsv for PLAN.md: slopes with 95 percent intervals, E2, E3."""
import csv
import statistics as st
from math import sqrt

rows = [r for r in csv.DictReader(open("results.tsv"), delimiter="\t")]
bad = [r["name"] + "/" + r["run"] for r in rows if r["ok"] != "1"]
print("failed runs:", bad or "none")
T = {18: 2.101, 13: 2.160, 8: 2.306, 3: 3.182}  # t quantiles 0.975


def med(sel, k):
    return st.median(float(r[k]) for r in sel)


def ols(xs, ys):
    n = len(xs); mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    res = [y - a - b * x for x, y in zip(xs, ys)]
    s = sqrt(sum(e * e for e in res) / (n - 2))
    se = s / sqrt(sxx)
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = 1 - sum(e * e for e in res) / ss_tot
    return a, b, se * T[n - 2], r2


print("\nE1: medians per file count (MB). ordered rss / commit | original rss / commit | groups")
for q in ("Q1", "Q2", "Q3"):
    for f in ("150", "300", "600", "1200"):
        o = [r for r in rows if r["experiment"] == "E1" and r["files"] == f and r["query"] == q and r["binary"] == "accept-groups"]
        b = [r for r in rows if r["experiment"] == "E1" and r["files"] == f and r["query"] == q and r["binary"] == "original"]
        print(f"  {q} {f:>5}: {med(o,'rss_mb'):6.0f} / {med(o,'mi_peak_commit_mb'):5.0f}  [rss range {min(int(r['rss_mb']) for r in o)}-{max(int(r['rss_mb']) for r in o)}]"
              f" | {med(b,'rss_mb'):5.0f} / {med(b,'mi_peak_commit_mb'):4.0f} | groups {o[0]['groups']}"
              f" | user {med(o,'user_s'):.2f} sys {med(o,'sys_s'):.2f}")

print("\nE1: excess over the original (ordered run minus median original), regressed on ordered groups")
for metric in ("rss_mb", "mi_peak_commit_mb"):
    for q in ("Q1", "Q2", "Q3"):
        xs, ys = [], []
        for f in ("150", "300", "600", "1200"):
            base = med([r for r in rows if r["experiment"] == "E1" and r["files"] == f and r["query"] == q and r["binary"] == "original"], metric)
            for r in rows:
                if r["experiment"] == "E1" and r["files"] == f and r["query"] == q and r["binary"] == "accept-groups":
                    xs.append(int(r["groups"])); ys.append(float(r[metric]) - base)
        a, b, ci, r2 = ols(xs, ys)
        print(f"  {metric:18} {q}: slope {b*1024:6.0f} KB/stream  [95% {(b-ci)*1024:6.0f} .. {(b+ci)*1024:6.0f}]  intercept {a:6.0f} MB  R2 {r2:.2f}  n={len(xs)}")

print("\nE2: Q3, 1200 files, outputs = target_partitions")
e2 = {"2": [r for r in rows if r["experiment"] == "E1" and r["files"] == "1200" and r["query"] == "Q3" and r["binary"] == "accept-groups"]}
for t in ("4", "8"):
    e2[t] = [r for r in rows if r["experiment"] == "E2" and r["target"] == t]
for t, sel in e2.items():
    rss = sorted(int(r["rss_mb"]) for r in sel); com = sorted(int(r["mi_peak_commit_mb"]) for r in sel)
    print(f"  outputs {t}: rss median {st.median(rss):.0f} {rss}  commit median {st.median(com):.0f} {com}  elapsed {med(sel,'elapsed_s'):.3f}")

print("\nE3: 1200 files, default purge delay against MIMALLOC_PURGE_DELAY=0")
for q in ("Q1", "Q3"):
    d = [r for r in rows if r["experiment"] == "E1" and r["files"] == "1200" and r["query"] == q and r["binary"] == "accept-groups"]
    z = [r for r in rows if r["experiment"] == "E3" and r["query"] == q]
    for lab, sel in (("default", d), ("purge 0", z)):
        rss = sorted(int(r["rss_mb"]) for r in sel); com = sorted(int(r["mi_peak_commit_mb"]) for r in sel)
        print(f"  {q} {lab:8}: rss {rss} median {st.median(rss):.0f} | commit {com} median {st.median(com):.0f} | elapsed {med(sel,'elapsed_s'):.3f}")
