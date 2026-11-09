"""Read-only owner-run runner preflight. No networking, secret reads or file writes.

Run only through a fresh approved pinned owner session as tr-ledger-backup.
Outputs capability flags, not configuration values. This is not live clearance.
"""

import importlib.util
import json
import os
import pwd
import re
import sys
from pathlib import Path


def inspect_location(config_path, expected_state, *, expected_user="tr-ledger-backup"):
    result = {
        "scope": "read-only-location-preflight",
        "live_clearance": False,
        "expected_user": pwd.getpwuid(os.geteuid()).pw_name == expected_user,
        "python_312_or_newer": sys.version_info >= (3, 12),
        "config_readable": False,
        "expected_state_directory": False,
        "state_writable_by_process": False,
        "existing_ledger_readable": False,
        "hmac_file_readable_without_reading": False,
        "ssh_alias_configured": False,
        "probe_process_ebay_variables_present": all(
            bool(os.environ.get(k)) for k in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET")
        ),
        "dependencies_present": {
            name: importlib.util.find_spec(name) is not None
            for name in ("sqlalchemy", "pydantic", "httpx", "PIL", "fastapi")
        },
    }
    if not result["expected_user"]:
        return result
    try:
        config_path = Path(config_path)
        if config_path.is_symlink() or config_path.stat().st_size > 16384:
            return result
        config = json.loads(config_path.read_text())
        result["config_readable"] = True
        state = Path(config["state_directory"])
        expected_state = Path(expected_state)
        result["expected_state_directory"] = (
            state == expected_state
            and state.is_dir()
            and not any(p.is_symlink() for p in (state, *state.parents))
        )
        if not result["expected_state_directory"]:
            return result
        result["state_writable_by_process"] = os.access(state, os.W_OK | os.X_OK)
        ledger = state / "ledger.db"
        result["existing_ledger_readable"] = (
            ledger.is_file() and not ledger.is_symlink() and os.access(ledger, os.R_OK)
        )
        hmac_file = Path(config["hmac_file"])
        result["hmac_file_readable_without_reading"] = (
            hmac_file.is_absolute()
            and hmac_file.is_file()
            and not any(p.is_symlink() for p in (hmac_file, *hmac_file.parents))
            and os.access(hmac_file, os.R_OK)
        )
        alias = config.get("ssh_alias")
        result["ssh_alias_configured"] = isinstance(alias, str) and bool(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", alias)
        )
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return result


if __name__ == "__main__":
    print(
        json.dumps(
            inspect_location("/etc/tr-ledger-backup/config.json", "/var/lib/tr-ledger-backup"),
            sort_keys=True,
        )
    )
