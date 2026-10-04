#!/usr/bin/env bash
# Build the two datafusion-cli binaries the lab compares, and record how.
#
# Usage: scripts/build_binaries.sh /path/to/datafusion-checkout
#
# The recorded commit is checked out in a separate, detached worktree;
# `original` is built as it is, `accept-groups` with
# patch/accept-extra-groups.patch applied. Each binary is copied to bin/ before
# the next build. What identifies the build is written to provenance/, which is
# meant to be committed with the results it produced: the commit, the toolchain as seen inside the worktree, the build
# command, the patch as applied, the build logs and the SHA-256 of the binaries.
# run_lab.sh refuses to run with binaries whose hashes differ from those.
set -euo pipefail

commit=e1aa7d956a5aa67452c9e8bd2a033599767055d8
checkout=${1:?path to a DataFusion git checkout}
root=$(cd "$(dirname "$0")/.." && pwd)
out=$root/bin
final=$root/provenance
prov=$(mktemp -d "$root/provenance.building.XXXXXX")     # renamed to provenance/ on success
worktree=$(mktemp -d "${TMPDIR:-/tmp}/datafusion-ordered-groups.XXXXXX")

mkdir -p "$out"
git -C "$checkout" worktree add --detach "$worktree" "$commit"
trap 'git -C "$checkout" worktree remove --force "$worktree"; rm -rf "$prov"' EXIT

build() {
  local variant=$1
  (cd "$worktree" && cargo build --release -p datafusion-cli) \
    > "$prov/build-$variant.log" 2>&1
  # copy beside the target and rename: replacing a binary that is running fails otherwise
  cp "$worktree/target/release/datafusion-cli" "$out/datafusion-cli-$variant-release.new"
  mv -f "$out/datafusion-cli-$variant-release.new" "$out/datafusion-cli-$variant-release"
}

build original
git -C "$worktree" apply "$root/patch/accept-extra-groups.patch"
build accept-groups
git -C "$worktree" diff > "$prov/applied.patch"

echo "$commit" > "$prov/datafusion-commit.txt"
(cd "$worktree" && { rustc --version --verbose; cargo --version; }) > "$prov/toolchain.txt"
echo "cargo build --release -p datafusion-cli" > "$prov/build-command.txt"
(cd "$root" && sha256sum bin/datafusion-cli-original-release bin/datafusion-cli-accept-groups-release \
  > "$prov/binaries.sha256")
date -u +%Y-%m-%dT%H:%M:%SZ > "$prov/built-at.txt"
# a build that fails leaves the previous provenance/ as it was
rm -rf "$final"
mv "$prov" "$final"
cat "$final/binaries.sha256"
