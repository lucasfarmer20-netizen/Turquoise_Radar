#!/bin/sh
# Build only. Does not provision, deploy, register or contact eBay.
set -eu
archive=${1:?Usage: package.sh NEW_OUTPUT.tar.gz}
[ ! -e "$archive" ] || { echo 'Output must be new' >&2; exit 1; }
# Includes the verified Node crypto helper, frozen dependency lock and this runbook.
tar --exclude='__pycache__' --exclude='*.pyc' -czf "$archive" \
    radar/ebay_deletion radar/__init__.py pyproject.toml uv.lock deploy/ebay-deletion \
    docs/JRI-012.md
