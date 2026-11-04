"""Synthetic owner-custody rehearsal; every gcloud subprocess is replaced locally."""

import hashlib
import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

SCRIPT = Path(__file__).resolve().parents[1] / "deploy/ebay-deletion/export-recovery.py"
SPEC = importlib.util.spec_from_file_location("synthetic_owner_custody", SCRIPT)
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)

PASSPHRASE = "SYNTHETIC owner recovery passphrase only 000000"
HMAC = "SYNTHETIC owner custody HMAC only 000000000000"
EXPECTED_HOSTS = {
    "tr-ebay-notify-01": "8560186650804972514",
    "tr-ledger-runner-01": "5443443387925882803",
}


@pytest.fixture(scope="module")
def synthetic_material():
    private = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    host_keys = {
        host: ed25519.Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
        .decode()
        for host in recovery.HOSTS
    }
    return {
        "private": pem,
        "public_sha256": hashlib.sha256(public).hexdigest(),
        "host_keys": host_keys,
    }


@pytest.fixture(autouse=True)
def forbid_external_commands_and_restore_umask(monkeypatch):
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append(args)
        raise AssertionError("Synthetic custody tests cannot execute external commands")

    monkeypatch.setattr(recovery.subprocess, "run", forbidden)
    old_umask = os.umask(0o077)
    try:
        yield attempts
    finally:
        os.umask(old_umask)


def owner_arguments(tmp_path, monkeypatch, *, one=None, two=None):
    key = tmp_path / "synthetic ssh key"
    key.write_text("SYNTHETIC placeholder: no real SSH connection")
    one = one or tmp_path / "media one" / "recovery.encrypted"
    two = two or tmp_path / "media two" / "recovery.encrypted"
    one.parent.mkdir(parents=True, exist_ok=True)
    two.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--owner-machine",
            "--ssh-key-file",
            str(key),
            "--copy-one",
            str(one),
            "--copy-two",
            str(two),
        ],
    )
    monkeypatch.setattr(recovery.getpass, "getpass", lambda _: PASSPHRASE)
    return key, one, two


def fake_cloud(monkeypatch, material, *, serial_override=None, fail_ssh=False):
    calls = []

    def run(command, **kwargs):
        assert command[0] == "gcloud"
        assert command[-2:] == ["--project=turquoiseradar", "--quiet"]
        assert kwargs == {"capture_output": True, "timeout": 120, "check": False}
        calls.append(command)
        if command[1:4] == ["compute", "instances", "describe"]:
            host = command[4]
            assert "--format=value(id)" in command
            raw = EXPECTED_HOSTS[host].encode()
        elif command[1:4] == ["compute", "instances", "get-serial-port-output"]:
            host = command[4]
            raw = (
                serial_override
                if serial_override is not None
                else ("SYNTHETIC boot output\n" + material["host_keys"][host] + "\n").encode()
            )
        elif command[1:3] == ["compute", "ssh"]:
            host = command[3]
            assert "--tunnel-through-iap" in command
            assert "--strict-host-key-checking=yes" in command
            assert "--ssh-flag=-oGlobalKnownHostsFile=/dev/null" in command
            assert "--ssh-flag=-oHostKeyAlgorithms=ssh-ed25519" in command
            option = next(v for v in command if v.startswith("--ssh-flag=-oUserKnownHostsFile="))
            known_hosts = Path(option.split("=", 2)[2])
            assert (
                f"compute.{EXPECTED_HOSTS[host]} {material['host_keys'][host]}\n"
                in known_hosts.read_text()
            )
            remote = next(
                v.removeprefix("--command=") for v in command if v.startswith("--command=")
            )
            parsed = shlex.split(remote)
            assert parsed[:5] == ["sudo", "-n", "python3", "-I", "-c"]
            assert len(parsed) == 6
            assert PASSPHRASE not in remote and HMAC not in remote
            record = (
                {"receiver_hmac": HMAC, "generation_date": "2000-01-01"}
                if host == "tr-ebay-notify-01"
                else {"backup_decryption_private_pem": material["private"]}
            )
            raw = json.dumps(record).encode()
            if fail_ssh:
                return subprocess.CompletedProcess(command, 1, raw, material["private"].encode())
        else:
            raise AssertionError("Unexpected external operation in synthetic rehearsal")
        assert "--zone=us-west1-b" in command
        return subprocess.CompletedProcess(command, 0, raw, b"")

    monkeypatch.setattr(recovery.subprocess, "run", run)
    return calls


def assert_sanitized_failure(capsys, material=None):
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "status": "failed",
        "error": "OWNER_RECOVERY_EXPORT_FAILED",
    }
    assert PASSPHRASE not in captured.out and HMAC not in captured.out
    if material:
        assert material["private"] not in captured.out


def test_owner_command_pins_authenticated_hosts_and_reports_verified_fingerprints(
    tmp_path, monkeypatch, capsys, synthetic_material
):
    key, one, two = owner_arguments(tmp_path, monkeypatch)
    calls = fake_cloud(monkeypatch, synthetic_material)
    recovery.main()
    captured = capsys.readouterr()
    receipt = json.loads(captured.out)
    assert captured.err == ""
    assert receipt["encrypted_copies_verified"] == 2
    assert receipt["hmac_id"] == hashlib.sha256(HMAC.encode()).hexdigest()[:16]
    assert receipt["encryption_public_sha256"] == synthetic_material["public_sha256"]
    assert receipt["physical_separation"] == "owner must verify placement"
    assert receipt["schema"] == "tr-offline-recovery-v1"
    assert len(calls) == 6
    ssh_calls = [c for c in calls if c[1:3] == ["compute", "ssh"]]
    assert len(ssh_calls) == 2
    assert all("--ssh-key-file=" + str(key) in command for command in ssh_calls)
    assert one.read_bytes() == two.read_bytes()
    for destination in [one, two]:
        encrypted = destination.read_bytes()
        assert encrypted.startswith(recovery.MAGIC)
        assert destination.stat().st_mode & 0o777 == 0o600
        assert HMAC.encode() not in encrypted
        assert synthetic_material["private"].encode() not in encrypted
        contents = json.loads(recovery.open_bundle(encrypted, PASSPHRASE))
        assert contents["receiver_hmac_record"]["receiver_hmac"] == HMAC
        assert (
            contents["backup_decryption_record"]["backup_decryption_private_pem"]
            == (synthetic_material["private"])
        )
    for private in [HMAC, PASSPHRASE, synthetic_material["private"]]:
        assert private not in captured.out
    with pytest.raises(InvalidTag):
        recovery.open_bundle(one.read_bytes(), "SYNTHETIC incorrect passphrase only")
    damaged = bytearray(two.read_bytes())
    damaged[-1] ^= 1
    with pytest.raises(InvalidTag):
        recovery.open_bundle(bytes(damaged), PASSPHRASE)


@pytest.mark.parametrize(
    "serial", [b"SYNTHETIC no host key", b"ssh-ed25519 AAAA\nssh-ed25519 BBBB\n"]
)
def test_missing_or_ambiguous_authenticated_host_key_stops_before_secret_read(
    tmp_path, monkeypatch, capsys, synthetic_material, serial
):
    _, one, two = owner_arguments(tmp_path, monkeypatch)
    calls = fake_cloud(monkeypatch, synthetic_material, serial_override=serial)
    with pytest.raises(SystemExit) as stopped:
        recovery.main()
    assert stopped.value.code == 2
    assert len(calls) == 2
    assert not one.exists() and not two.exists()
    assert_sanitized_failure(capsys, synthetic_material)


def test_failed_remote_command_cannot_print_captured_recovery_secrets(
    tmp_path, monkeypatch, capsys, synthetic_material
):
    _, one, two = owner_arguments(tmp_path, monkeypatch)
    calls = fake_cloud(monkeypatch, synthetic_material, fail_ssh=True)
    with pytest.raises(SystemExit) as stopped:
        recovery.main()
    assert stopped.value.code == 2
    assert len(calls) == 3
    assert not one.exists() and not two.exists()
    assert_sanitized_failure(capsys, synthetic_material)


@pytest.mark.parametrize("collision", ["same", "alias", "existing"])
def test_destination_collision_fails_before_passphrase_or_cloud_access(
    tmp_path, monkeypatch, capsys, collision, forbid_external_commands_and_restore_umask
):
    original = tmp_path / "media" / "recovery.encrypted"
    other = original
    if collision == "alias":
        original.parent.mkdir()
        alias = tmp_path / "media-alias"
        alias.symlink_to(original.parent, target_is_directory=True)
        other = alias / original.name
    elif collision == "existing":
        original.parent.mkdir()
        original.write_bytes(b"SYNTHETIC original recovery copy")
        other = tmp_path / "second-media" / original.name
    _, one, two = owner_arguments(tmp_path, monkeypatch, one=original, two=other)

    prompts = []

    def forbidden_prompt(*args):
        prompts.append(args)
        raise AssertionError("Preflight must reject unsafe destinations before prompting")

    monkeypatch.setattr(recovery.getpass, "getpass", forbidden_prompt)
    with pytest.raises(SystemExit) as stopped:
        recovery.main()
    assert stopped.value.code == 2
    assert prompts == []
    assert forbid_external_commands_and_restore_umask == []
    if collision == "existing":
        assert one.read_bytes() == b"SYNTHETIC original recovery copy"
        assert not two.exists()
    else:
        assert not one.exists()
    assert_sanitized_failure(capsys)


def test_second_copy_readback_corruption_never_reports_custody_success(
    tmp_path, monkeypatch, capsys, synthetic_material
):
    _, one, two = owner_arguments(tmp_path, monkeypatch)
    fake_cloud(monkeypatch, synthetic_material)
    original_read = Path.read_bytes

    def readback(path):
        raw = original_read(path)
        if path == two:
            return raw[:-1] + bytes([raw[-1] ^ 1])
        return raw

    monkeypatch.setattr(Path, "read_bytes", readback)
    with pytest.raises(SystemExit) as stopped:
        recovery.main()
    assert stopped.value.code == 2
    assert one.exists() and two.exists()
    assert one.read_bytes().startswith(recovery.MAGIC)
    assert_sanitized_failure(capsys, synthetic_material)
