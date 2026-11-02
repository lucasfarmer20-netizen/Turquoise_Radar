"""Forced, host-pinned SSH command: respond to one fresh nonce with current signed events.

The restricted command needs read access to the receiver queue and the protected
HMAC key. Load that key via a root-controlled wrapper/environment, never SSH input.
No forwarding, PTY or interactive shell. No private workbench data lives here.
"""

import os
import sqlite3
import sys

from radar.ebay_deletion.sync import receiver_bundle

try:
    nonce = sys.stdin.buffer.readline(66).decode("ascii").strip()
    secret = os.environ["EBAY_DELETION_HMAC_KEY"].encode()
    if len(secret) < 32:
        raise ValueError()
    sys.stdout.buffer.write(receiver_bundle("/var/lib/ebay-receiver/queue.db", secret, nonce))
except (KeyError, ValueError, OSError, sqlite3.Error):
    raise SystemExit(2) from None
