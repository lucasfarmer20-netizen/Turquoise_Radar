"""Fixed sudo/SSH command: one challenge, one current signed receiver ledger.

Executed as ebay-receiver, never root, using Python -I and a root-owned code tree.
The SSH account cannot choose a command, queue path, key, module or output path.
"""

import os
import re
import sqlite3
import stat
import sys
from pathlib import Path

CONFIG = Path("/etc/ebay-receiver/receiver.env")
QUEUE = Path("/var/lib/ebay-receiver/queue.db")
CODE = Path("/opt/ebay-receiver")


def protected_secret(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o027:
        raise ValueError("Unsafe receiver configuration")
    # Read only the HMAC assignment; do not execute shell or inherit caller secrets.
    values = [
        line.removeprefix("EBAY_DELETION_HMAC_KEY=")
        for line in path.read_text().splitlines()
        if line.startswith("EBAY_DELETION_HMAC_KEY=")
    ]
    if len(values) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", values[0]):
        raise ValueError("Invalid receiver key")
    return values[0].encode()


def main():
    try:
        # ForceCommand ignores SSH_ORIGINAL_COMMAND; reject it as an additional guard.
        if len(sys.argv) != 1 or os.environ.get("SSH_ORIGINAL_COMMAND", ""):
            raise ValueError("Unexpected command")
        nonce = sys.stdin.buffer.readline(66).decode("ascii")
        if not re.fullmatch(r"[a-f0-9]{64}\n", nonce):
            raise ValueError("Invalid synchronization challenge")
        secret = protected_secret(CONFIG)
        # -I intentionally excludes the working directory and PYTHONPATH.
        sys.path.insert(0, str(CODE))
        from radar.ebay_deletion.sync import receiver_bundle

        sys.stdout.buffer.write(receiver_bundle(QUEUE, secret, nonce.rstrip("\n")))
    except (KeyError, ValueError, OSError, sqlite3.Error):
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
