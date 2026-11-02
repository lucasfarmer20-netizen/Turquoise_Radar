"""Synthetic-only owner restore-reader timing; no cloud request or production key."""

import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

PATH = Path(__file__).resolve().parents[1] / "deploy/ebay-deletion/time-recovery.py"
spec = importlib.util.spec_from_file_location("owner_recovery_timing", PATH)
timing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timing)


@pytest.fixture(autouse=True)
def no_live_access(monkeypatch):
    for name in (
        "OIC_MANIFEST_PATH",
        "CLOUD_SHELL",
        "DEVSHELL_PROJECT_ID",
        "CLOUDSDK_AUTH_ACCESS_TOKEN",
        "CLOUDSDK_AUTH_ACCESS_TOKEN_FILE",
        "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE",
        "CLOUDSDK_AUTH_LOGIN_CONFIG_FILE",
    ):
        monkeypatch.delenv(name, raising=False)

    def refuse(*args, **kwargs):
        raise AssertionError("Synthetic tests must mock every external command")

    monkeypatch.setattr(timing.subprocess, "run", refuse)


@pytest.fixture(scope="module")
def keys():
    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    return (
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        ),
    )


@pytest.fixture
def synthetic(tmp_path, monkeypatch, keys):
    public, private, der = keys
    secret = b"SYNTHETIC-owner-recovery-HMAC-0000000000000000"
    password = "SYNTHETIC offline passphrase for tests only"
    monkeypatch.setattr(timing, "HMAC_ID", hashlib.sha256(secret).hexdigest()[:16])
    monkeypatch.setattr(timing, "PUBLIC_SHA256", hashlib.sha256(der).hexdigest())
    monkeypatch.setattr(timing.getpass, "getpass", lambda prompt: password)
    bundle = {
        "receiver_hmac_record": {"receiver_hmac": secret.decode()},
        "backup_decryption_record": {"backup_decryption_private_pem": private.decode()},
        "metadata": {"fixture": "SYNTHETIC", "hmac_id": "deliberately not trusted"},
    }
    copy = tmp_path / "synthetic-recovery.encrypted"
    copy.write_bytes(timing.EXPORT.seal(json.dumps(bundle).encode(), password))
    copy.chmod(0o600)
    queue = timing.CLOUD.BACKUP.Queue(tmp_path / "synthetic-source.db")
    queue.bind(secret)
    queue.accept("a" * 64, {"userId": "b" * 64})
    verified = tmp_path / "synthetic-source-backup"
    timing.CLOUD.BACKUP.backup_ledger(queue.path, verified, secret)
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as stream:
        for name in ("ledger.db", "manifest.json"):
            stream.add(verified / name, arcname=name)
    ciphertext = timing.CLOUD.encrypt(archive.getvalue(), public)
    monkeypatch.setattr(timing, "CIPHERTEXT_SHA256", hashlib.sha256(ciphertext).hexdigest())
    parent = tmp_path / "owner-private"
    parent.mkdir(mode=0o700)
    return {
        "copy": copy,
        "destination": parent / "new-drill",
        "ciphertext": ciphertext,
        "secret": secret,
        "private": private,
        "password": password,
    }


def fake_download(monkeypatch, synthetic, calls):
    def run(args, **kwargs):
        calls.append((args, kwargs))
        target = Path(args[5])
        assert not target.exists()
        with target.open("xb") as stream:
            stream.write(synthetic["ciphertext"])
        target.chmod(0o600)
        return subprocess.CompletedProcess(args, 0, stdout=b"synthetic private output", stderr=b"")

    monkeypatch.setattr(timing.subprocess, "run", run)


def invoke_main(monkeypatch, synthetic):
    monkeypatch.setattr(timing.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(timing.sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(PATH),
            "--owner-machine",
            "--recovery-copy",
            str(synthetic["copy"]),
            "--destination",
            str(synthetic["destination"]),
        ],
    )
    timing.main()


def test_owner_download_through_real_synthetic_quarantine_is_timed(synthetic, monkeypatch, capsys):
    calls = []
    fake_download(monkeypatch, synthetic, calls)
    stamps = iter(["2026-11-02T23:10:00+00:00", "2026-11-02T23:10:07+00:00"])
    ticks = iter([100.0, 107.25])
    monkeypatch.setattr(timing, "utc_now", lambda: next(stamps))
    monkeypatch.setattr(timing.time, "monotonic", lambda: next(ticks))
    invoke_main(monkeypatch, synthetic)
    printed = capsys.readouterr().out
    report = json.loads(printed)
    assert report["started_utc"] == "2026-11-02T23:10:00+00:00"
    assert report["completed_utc"] == "2026-11-02T23:10:07+00:00"
    assert report["duration_seconds"] == 7.25
    assert report["event_count"] == 1 and report["receipt_count"] == 0
    assert report["integrity"] == "ok" and report["key_bound"] is True
    assert report["offline_copy_only"] is True
    assert report["current_receiver_proof"] is False
    assert report["operational_restore_performed"] is False
    assert report["offline_bundle_unlock_included"] is False
    args, kwargs = calls[0]
    assert args[:5] == ["gcloud", "storage", "cp", "--no-clobber", timing.SOURCE]
    assert "--account=" + timing.OWNER in args
    assert "--impersonate-service-account=" + timing.READER in args
    assert kwargs["env"]["CLOUDSDK_CORE_DISABLE_FILE_LOGGING"] == "true"
    assert kwargs["env"]["CLOUDSDK_CORE_LOG_HTTP"] == "false"
    assert kwargs["capture_output"] is True
    for value in (
        synthetic["secret"].decode(),
        synthetic["private"].decode(),
        synthetic["password"],
    ):
        assert value not in printed
    assert "synthetic private output" not in printed
    created = list(synthetic["destination"].rglob("*"))
    assert {p.name for p in created} == {
        "download.trlb",
        "quarantine",
        "ledger.db",
        "manifest.json",
    }
    for path in created:
        assert path.stat().st_mode & 0o077 == 0
        if path.is_file():
            raw = path.read_bytes()
            assert synthetic["secret"] not in raw and synthetic["private"] not in raw


@pytest.mark.parametrize(
    "variable", ["OIC_MANIFEST_PATH", "CLOUD_SHELL", "CLOUDSDK_AUTH_ACCESS_TOKEN"]
)
def test_managed_environment_or_token_override_refused_before_unlock(monkeypatch, variable):
    monkeypatch.setenv(variable, "SYNTHETIC")
    monkeypatch.setattr(timing, "unlock", lambda _: pytest.fail("Must refuse before key access"))
    with pytest.raises(ValueError):
        timing.timed_recovery(Path("unused"), Path("unused"))


def test_wrong_fingerprint_refused_without_download(synthetic, monkeypatch):
    monkeypatch.setattr(timing, "HMAC_ID", "wrong")
    with pytest.raises(ValueError):
        timing.timed_recovery(synthetic["copy"], synthetic["destination"])
    assert not synthetic["destination"].exists()


def test_download_failure_never_emits_completion_or_private_diagnostics(
    synthetic, monkeypatch, capsys
):
    monkeypatch.setattr(
        timing.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, stdout=synthetic["private"], stderr=synthetic["secret"]
        ),
    )
    with pytest.raises(SystemExit) as exit_info:
        invoke_main(monkeypatch, synthetic)
    assert exit_info.value.code == 2
    assert json.loads(capsys.readouterr().out) == {
        "status": "failed",
        "error": "OWNER_TIMED_RECOVERY_FAILED",
    }
    assert not (synthetic["destination"] / "quarantine").exists()


def test_corrupt_download_never_reaches_quarantine(synthetic, monkeypatch):
    calls = []
    fake_download(monkeypatch, synthetic, calls)
    monkeypatch.setattr(timing, "CIPHERTEXT_SHA256", "wrong")
    monkeypatch.setattr(timing.CLOUD, "quarantine", lambda *args: pytest.fail("Bad checksum"))
    with pytest.raises(ValueError):
        timing.timed_recovery(synthetic["copy"], synthetic["destination"])


@pytest.mark.parametrize("kind", ["existing", "symlink", "public_parent"])
def test_destination_must_be_new_private_and_without_symlinks(tmp_path, kind):
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    destination = parent / "new"
    if kind == "existing":
        destination.mkdir(mode=0o700)
    elif kind == "symlink":
        linked = tmp_path / "linked"
        linked.symlink_to(parent)
        destination = linked / "new"
    else:
        parent.chmod(0o755)
    with pytest.raises((ValueError, OSError)):
        timing.new_private_directory(destination)


def test_owner_acknowledgement_required(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [str(PATH), "--recovery-copy=a", "--destination=b"])
    with pytest.raises(SystemExit) as exit_info:
        timing.main()
    assert exit_info.value.code == 2
    assert "--owner-machine" in capsys.readouterr().err


def test_noninteractive_input_refused_without_getpass_fallback(monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [str(PATH), "--owner-machine", "--recovery-copy=unused", "--destination=unused"],
    )
    monkeypatch.setattr(timing.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(timing.getpass, "getpass", lambda _: pytest.fail("Must not prompt"))
    with pytest.raises(SystemExit) as exit_info:
        timing.main()
    assert exit_info.value.code == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "status": "failed",
        "error": "OWNER_TIMED_RECOVERY_FAILED",
    }


def test_private_file_rejects_symlink_and_excess_permissions(tmp_path):
    value = tmp_path / "value"
    value.write_bytes(b"SYNTHETIC encrypted copy")
    value.chmod(0o644)
    with pytest.raises(ValueError):
        timing.private_bytes(value, 1024)
    value.chmod(0o600)
    linked = tmp_path / "linked"
    linked.symlink_to(value)
    with pytest.raises(OSError):
        timing.private_bytes(linked, 1024)
    assert timing.private_bytes(value, 1024) == b"SYNTHETIC encrypted copy"
