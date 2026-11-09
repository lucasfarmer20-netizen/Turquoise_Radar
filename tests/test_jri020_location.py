"""Synthetic read-only capability reporting; no private-host access."""

import importlib.util
import json
import os
import pwd
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "jri020_location", Path(__file__).parents[1] / "scripts/check-jri020-location.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
USER = pwd.getpwuid(os.geteuid()).pw_name


def test_location_reports_flags_without_reading_or_disclosing_key(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    (state / "ledger.db").write_bytes(b"SYNTHETIC not a real ledger")
    key = tmp_path / "synthetic-key"
    key.write_text("SYNTHETIC-secret-must-not-be-read")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "state_directory": str(state),
                "hmac_file": str(key),
                "ssh_alias": "synthetic-receiver",
            }
        )
    )
    original = Path.read_text

    def guarded(path, *args, **kwargs):
        assert path == config
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    result = MODULE.inspect_location(config, state, expected_user=USER)
    assert result["config_readable"] and result["existing_ledger_readable"]
    assert result["hmac_file_readable_without_reading"] and result["ssh_alias_configured"]
    assert result["live_clearance"] is False
    assert "SYNTHETIC-secret" not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)


def test_wrong_user_missing_config_and_symlink_do_not_pass(tmp_path):
    absent = tmp_path / "absent"
    assert not MODULE.inspect_location(absent, tmp_path, expected_user="SYNTHETIC-nobody")[
        "expected_user"
    ]
    assert not MODULE.inspect_location(absent, tmp_path, expected_user=USER)["config_readable"]
    target = tmp_path / "target"
    target.write_text("{}")
    link = tmp_path / "link"
    link.symlink_to(target)
    assert not MODULE.inspect_location(link, tmp_path, expected_user=USER)["config_readable"]
