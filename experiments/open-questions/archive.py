"""Move the outputs of the last run of every experiment here into a dated subdirectory.

Run from the repository root, before running the experiments again:
  python experiments/open-questions/archive.py 2026-10-04
  python experiments/open-questions/archive.py --check 2026-10-04   # only say whether it can be done

The scripts refuse to overwrite earlier results; this is the step that makes
room for a new run and keeps the old one. SQL files of the queries, scripts
and plans stay. Every target is checked before anything moves, so the label
either applies to all the experiments or to none.
"""
import re
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
    "fd-limit": ["nofile-*", "*.sql", "results.tsv"],
}
KEEP_IN_PLACE = {"run.py"}      # depth/batch/run.py is a script inside an output directory


def movable(path):
    """The children of an output directory to move, or the path itself for a file."""
    if not path.is_dir():
        return [path]
    return [c for c in sorted(path.iterdir()) if c.name not in KEEP_IN_PLACE]


def main():
    args = [a for a in sys.argv[1:] if a != "--check"]
    if len(args) != 1 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args[0]):
        raise SystemExit("usage: archive.py [--check] <label: letters, digits, dot, dash, underscore>")
    label, check_only = args[0], "--check" in sys.argv[1:]
    plan = {}
    for experiment, patterns in OUTPUTS.items():
        found = [p for pat in patterns for p in sorted((HERE / experiment).glob(pat))
                 if movable(p)]
        if found:
            plan[experiment] = found
    taken = [HERE / e / label for e in plan if (HERE / e / label).exists()]
    if taken:
        raise SystemExit("exists, choose another label: " + ", ".join(str(t) for t in taken))
    if check_only:
        print(f"label {label} is free; {len(plan)} experiment(s) have outputs to move")
        return
    for experiment, found in plan.items():
        target = HERE / experiment / label
        target.mkdir()
        for path in found:
            if path.is_dir() and any((path / k).exists() for k in KEEP_IN_PLACE):
                (target / path.name).mkdir()
                for child in movable(path):
                    shutil.move(str(child), str(target / path.name / child.name))
            else:
                shutil.move(str(path), str(target / path.name))
        print(f"{experiment}: {len(found)} item(s) moved to {target.relative_to(HERE)}")


if __name__ == "__main__":
    main()
