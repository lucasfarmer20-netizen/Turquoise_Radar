#!/bin/sh
# Immutable private-runner package; never includes working-tree/private files.
set -eu
archive=${1:?Usage: package-backup.sh NEW_OUTPUT.tar.gz REVIEWED_COMMIT}
[ ! -e "$archive" ] || { echo 'Output must be new' >&2; exit 1; }
archive_dir=$(cd -- "$(dirname -- "$archive")" && pwd)
archive="$archive_dir/$(basename -- "$archive")"
repo_root=$(git rev-parse --show-toplevel)
commit=$(git -C "$repo_root" rev-parse --verify "${2:?Supply reviewed commit}^{commit}")
set -- \
    radar/__init__.py \
    radar/ebay_deletion/__init__.py \
    radar/ebay_deletion/store.py \
    radar/ebay_deletion/sync.py \
    deploy/ebay-deletion/backup-ledger.py \
    deploy/ebay-deletion/cloud-backup.py \
    deploy/ebay-deletion/backup.service \
    deploy/ebay-deletion/backup.timer \
    deploy/ebay-deletion/backup-age.service \
    deploy/ebay-deletion/backup-age.timer
for path do
    mode=$(git -C "$repo_root" ls-tree "$commit" -- "$path" | cut -d ' ' -f 1)
    case "$mode" in
        100644|100755) ;;
        *) echo 'Reviewed package file missing or not regular' >&2; exit 1 ;;
    esac
done
git -C "$repo_root" archive --format=tar.gz --output="$archive" "$commit" -- "$@"
