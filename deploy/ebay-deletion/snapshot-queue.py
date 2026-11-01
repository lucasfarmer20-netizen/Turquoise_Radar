"""Run on receiver over authenticated, host-pinned SSH; emits a consistent SQLite snapshot.

Give the pull-only SSH account read access to queue.db. No workbench or shell access
is needed. Pin this script as its forced command; do not expose it over public HTTP.
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as directory:
    target = Path(directory) / "snapshot.db"
    with sqlite3.connect("file:/var/lib/ebay-receiver/queue.db?mode=ro", uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)
    with target.open("rb") as stream:
        while chunk := stream.read(65536):
            sys.stdout.buffer.write(chunk)
