#!/usr/bin/env bash
# Build the two datafusion-cli binaries compared in RESULTS.md.
#
# Usage: scripts/build_binaries.sh /path/to/datafusion-checkout [out-dir]
#
# The checkout is reset to the recorded commit in a separate, detached worktree;
# `original` is built as is, `accept-groups` with patch/accept-extra-groups.patch
# applied. Each binary is copied out of target/ before the next build, and the
# SHA-256 of both is written next to them. Round 3 of RESULTS.md was built this
# way with rustc 1.98.1; see results/round-3-rebuilt/toolchain.txt.
set -euo pipefail

commit=e1aa7d956a5aa67452c9e8bd2a033599767055d8
checkout=${1:?path to a DataFusion git checkout}
root=$(cd "$(dirname "$0")/.." && pwd)
out=${2:-$root/bin}
worktree=$(mktemp -d "${TMPDIR:-/tmp}/datafusion-ordered-groups.XXXXXX")

mkdir -p "$out"
git -C "$checkout" worktree add --detach "$worktree" "$commit"
trap 'git -C "$checkout" worktree remove --force "$worktree"' EXIT

build() {
  local variant=$1
  (cd "$worktree" && cargo build --release -p datafusion-cli) \
    > "$out/build-$variant.log" 2>&1
  cp "$worktree/target/release/datafusion-cli" "$out/datafusion-cli-$variant-release"
}

build original
git -C "$worktree" apply "$root/patch/accept-extra-groups.patch"
build accept-groups
git -C "$worktree" diff > "$out/applied.patch"

{ rustc --version --verbose; cargo --version; } > "$out/toolchain.txt"
echo "cargo build --release -p datafusion-cli" > "$out/build-command.txt"
(cd "$out" && sha256sum datafusion-cli-*-release > binaries.sha256)
cat "$out/binaries.sha256"
