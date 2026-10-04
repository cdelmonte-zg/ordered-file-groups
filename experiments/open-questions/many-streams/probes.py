"""The first probes of the many-stream memory: what moves the RSS and what does not.

Run from the repository root:
  python experiments/open-questions/many-streams/probes.py

1200 files with total overlap, 256 MB, three recorded runs each after a
warm-up, interleaved: the deduplication query with the ordered plan as it
is, with plain Utf8 strings, with batches of 1024 rows; and the ORDER BY
query with the ordered and with the original plan. Writes the outputs to
probes/, probes.tsv and probes-summary.txt.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import fresh, interleaved, timed_run, write_tsv  # noqa: E402

OUT = HERE / "probes"
OUT.mkdir(exist_ok=True)
RESULTS = fresh(HERE / "probes.tsv", outputs=OUT)
RUNS = 3
# label, variant, SQL file
PROBES = [
    ("dedup query", "accept-groups", "base.sql"),
    ("dedup query, strings as plain Utf8", "accept-groups", "noview.sql"),
    ("dedup query, batch size 1024 instead of 8192", "accept-groups", "batch1024.sql"),
    ("ORDER BY only, ordered plan", "accept-groups", "q1.sql"),
    ("ORDER BY only, original plan", "original", "q1.sql"),
]


def one(probe, tag):
    label, variant, sql = probe
    row = timed_run(variant, HERE / sql, OUT / f"{Path(sql).stem}-{variant}-{tag}")
    return {"probe": label, "variant": variant, "run": tag, **row}


rows = interleaved(PROBES, RUNS, one)
write_tsv(RESULTS, rows)
lines = ["probe\tcompleted\tscan groups\tprocess RSS (MB)\telapsed (s)"]
for label, variant, _ in PROBES:
    sel = [r for r in rows if r["probe"] == label]
    ok = [r for r in sel if r["ok"]]
    rss = ", ".join(str(r["rss_mb"]) for r in ok) or "-"
    times = sorted(float(r["elapsed_s"]) for r in ok)
    span = f"{times[0]:.2f} to {times[-1]:.2f}" if times else "-"
    groups = ", ".join(sorted({r["scan_groups"] for r in ok})) or "-"
    lines.append(f"{label}\t{len(ok)} of {len(sel)}\t{groups}\t{rss}\t{span}")
text = "\n".join(lines) + "\n"
(HERE / "probes-summary.txt").write_text(text)
print(text)
