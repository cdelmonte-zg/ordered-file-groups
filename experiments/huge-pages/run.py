"""How much of the resident memory is transparent huge pages.

Run from the repository root:
  python experiments/huge-pages/run.py [--out DIR]

The peak RSS counts whole pages. Where the kernel backs an allocation with a
2 MB page, the whole page is resident as soon as one byte of it is touched.
This experiment runs the many-stream cases twice, as the machine is set and
with transparent huge pages switched off for the measured process alone
(prctl PR_SET_THP_DISABLE, no privilege, nothing changed on the machine), and
records the peak RSS, the minor page faults and the times.

256 MB, five recorded runs per configuration after a warm-up, interleaved:
the deduplication with the ordered and the original plan at 150, 300, 600 and
1200 files with total overlap; at 1200 files also with 4 and 8 outputs, and
the ORDER BY query with both plans; and the base case of the matrix.
Writes every output and results.tsv.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import interleaved, output_dir, sql_for, timed_run, write_tsv  # noqa: E402

OUT = output_dir("huge-pages")
RUNS = 5


def many(files):
    return f"df-16919-partial-{files}-depth-{files}-rank"


# name, case, dataset, query, variant, target
configs = []
for files in (150, 300, 600, 1200):
    for variant in ("original", "accept-groups"):
        configs.append((f"dedup-{files}-{variant}", f"deduplication, {files} files", many(files), "Q3", variant, 2))
for target in (4, 8):
    configs.append((f"dedup-1200-accept-groups-t{target}", f"deduplication, 1200 files, {target} outputs",
                    many(1200), "Q3", "accept-groups", target))
for variant in ("original", "accept-groups"):
    configs.append((f"order-1200-{variant}", "ORDER BY, 1200 files", many(1200), "Q1", variant, 2))
    configs.append((f"base-{variant}", "deduplication, base case (12 files)", "df-16919-partial-12-depth-4",
                    "Q3", variant, 2))
for name, _, dataset, query, _, target in configs:
    (OUT / f"{name}.sql").write_text(sql_for(dataset, query, target=target))
cells = [(c, pages) for c in configs for pages in (True, False)]


def one(cell, tag):
    (name, case, dataset, query, variant, target), pages = cell
    mode = "as set" if pages else "off"
    row = timed_run(variant, OUT / f"{name}.sql", OUT / f"{name}-thp-{mode.replace(' ', '-')}-{tag}",
                    huge_pages=pages)
    return {"name": name, "case": case, "variant": variant, "target": target,
            "huge_pages": mode, "run": tag, **row}


write_tsv(OUT / "results.tsv", interleaved(cells, RUNS, one))
