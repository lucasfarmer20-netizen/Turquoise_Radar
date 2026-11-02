#!/bin/sh
# Build only. Does not provision, deploy, register or contact eBay.
# Only the explicitly reviewed files below, from an immutable commit, are packaged.
set -eu
archive=${1:?Usage: package.sh NEW_OUTPUT.tar.gz [REVIEWED_COMMIT]}
[ ! -e "$archive" ] || { echo 'Output must be new' >&2; exit 1; }
archive_dir=$(cd -- "$(dirname -- "$archive")" && pwd)
archive="$archive_dir/$(basename -- "$archive")"
repo_root=$(git rev-parse --show-toplevel)
commit=$(git -C "$repo_root" rev-parse --verify "${2:-HEAD}^{commit}")
set -- \
    radar/__init__.py \
    radar/ebay_deletion/__init__.py \
    radar/ebay_deletion/cli.py \
    radar/ebay_deletion/processing.py \
    radar/ebay_deletion/protocol.py \
    radar/ebay_deletion/receiver.py \
    radar/ebay_deletion/store.py \
    radar/ebay_deletion/sync.py \
    radar/ebay_deletion/verify.cjs \
    pyproject.toml \
    uv.lock \
    deploy/ebay-deletion/Caddyfile \
    deploy/ebay-deletion/inventory.example.json \
    deploy/ebay-deletion/package.sh \
    deploy/ebay-deletion/receiver.env.example \
    deploy/ebay-deletion/receiver.service \
    deploy/ebay-deletion/snapshot-queue.py \
    docs/JRI-012.md
# Refuse missing paths or symlinks; no directory inclusion or working-tree reads.
for path do
    mode=$(git -C "$repo_root" ls-tree "$commit" -- "$path" | cut -d ' ' -f 1)
    case "$mode" in
        100644|100755) ;;
        *) echo 'Reviewed package file missing or not a regular file' >&2; exit 1 ;;
    esac
done
git -C "$repo_root" archive --format=tar.gz --output="$archive" "$commit" -- "$@"
