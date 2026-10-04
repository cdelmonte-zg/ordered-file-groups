"""Move the outputs of the last run of every experiment here into a dated subdirectory.

Run from the repository root, before running the experiments again:
  python experiments/open-questions/archive.py 2026-10-04

The scripts refuse to overwrite earlier results; this is the step that makes
room for a new run and keeps the old one. SQL files, scripts and plans stay.
"""
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# experiment directory -> what a run writes into it
OUTPUTS = {
    "string-view-both": ["runs", "results.tsv", "summary.txt"],
    "many-streams": ["runs-plan", "results.tsv", "analysis.txt", "probes", "probes.tsv",
                     "probes-summary.txt"],
    "depth": ["batch", "probes", "probes.tsv", "probes-summary.txt"],
    "fd-limit": ["nofile-*", "case.sql", "results.tsv"],
}
KEEP_IN_PLACE = {"run.py"}      # depth/batch/run.py is a script inside an output directory


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: archive.py <label, for example the date of the run>")
    label = sys.argv[1]
    for experiment, patterns in OUTPUTS.items():
        target = HERE / experiment / label
        found = [p for pat in patterns for p in sorted((HERE / experiment).glob(pat))]
        if not found:
            continue
        if target.exists():
            raise SystemExit(f"{target} exists: choose another label")
        target.mkdir()
        for path in found:
            if path.is_dir() and any((path / k).exists() for k in KEEP_IN_PLACE):
                (target / path.name).mkdir()
                for child in sorted(path.iterdir()):
                    if child.name not in KEEP_IN_PLACE:
                        shutil.move(str(child), str(target / path.name / child.name))
            else:
                shutil.move(str(path), str(target / path.name))
        print(f"{experiment}: {len(found)} item(s) moved to {target.relative_to(HERE)}")


if __name__ == "__main__":
    main()
