Round 4 uses the binaries of round 3 unchanged: built on 2026-09-29 from commit
e1aa7d956 in a detached worktree, `original` as is and `accept-groups` with
`accept-extra-groups.patch`; see `build-*.log`, `toolchain.txt` and
`binaries.sha256` (verified with `sha256sum -c` before the run). What changes
from round 3 is the data: the datasets were regenerated with
`scripts/generate_base.py`, the generator of this repository, in place of the
one derived from the issue reporter's script.
