"""Synthetic-only cloud backup security and failure regressions."""

import importlib.util
import json
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

PATH = Path(__file__).resolve().parents[1] / "deploy/ebay-deletion/cloud-backup.py"
spec = importlib.util.spec_from_file_location("cloud_backup", PATH)
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


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
    )


def test_encryption_authenticated_randomized_and_independent(keys):
    public, private = keys
    raw = b"synthetic ledger; no real owner data"
    first, second = backup.encrypt(raw, public), backup.encrypt(raw, public)
    assert first != second and raw not in first
    assert backup.decrypt(first, private) == raw
    changed = first[:-1] + bytes([first[-1] ^ 1])
    with pytest.raises(InvalidTag):
        backup.decrypt(changed, private)
    with pytest.raises(ValueError):
        backup.decrypt(first, public)
    with pytest.raises(ValueError):
        backup.decrypt(b"not an envelope", private)


@pytest.mark.parametrize("age", [0, 3600, 5400])
def test_age_within_threshold(tmp_path, age):
    receipt = tmp_path / "success.json"
    receipt.write_text(json.dumps({"completed_at": 10000 - age}))
    backup.check_age(receipt, now=10000)


@pytest.mark.parametrize("stamp", [4599, 10001, "9000", True])
def test_age_rejects_stale_future_and_invalid_receipts(tmp_path, stamp):
    receipt = tmp_path / "success.json"
    receipt.write_text(json.dumps({"completed_at": stamp}))
    with pytest.raises(ValueError):
        backup.check_age(receipt, now=10000)


def test_cycle_stops_before_backup_or_upload_without_fresh_proof(tmp_path, monkeypatch):
    keyfile = tmp_path / "hmac"
    keyfile.write_bytes(b"synthetic-hmac-key" * 4)
    calls = []

    def fail(*args):
        calls.append("fresh-proof")
        raise ValueError("synthetic unavailable receiver")

    monkeypatch.setattr(backup, "maintenance_ledger", fail)
    monkeypatch.setattr(backup, "upload", lambda *args: calls.append("upload"))
    with pytest.raises(ValueError):
        backup.cycle(
            {"state_directory": str(tmp_path), "hmac_file": str(keyfile), "ssh_alias": "synthetic"}
        )
    assert calls == ["fresh-proof"]
    assert not (tmp_path / "success.json").exists()


def test_upload_uses_create_precondition_and_response_checksum(monkeypatch):
    raw = b"synthetic encrypted bytes"
    calls = []

    def request(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("email"):
            return backup.IDENTITY.encode()
        if url.endswith("token"):
            return b'{"access_token":"synthetic-token"}'
        return json.dumps(
            {
                "name": "hourly/synthetic.trlb",
                "bucket": backup.BUCKET,
                "generation": "123",
                "size": str(len(raw)),
                "md5Hash": backup.base64.b64encode(backup.hashlib.md5(raw).digest()).decode(),
            }
        ).encode()

    monkeypatch.setattr(backup, "request", request)
    backup.upload(raw, "hourly/synthetic.trlb")
    assert len(calls) == 3
    assert "ifGenerationMatch=0" in calls[-1][0]
    assert calls[-1][1]["data"] == raw
    assert all("DELETE" not in url for url, _ in calls)
    monkeypatch.setattr(
        backup,
        "request",
        lambda url, **kwargs: backup.IDENTITY.encode()
        if url.endswith("email")
        else b'{"access_token":"synthetic-token","md5Hash":"incorrect"}',
    )
    with pytest.raises(ValueError):
        backup.upload(raw, "hourly/synthetic.trlb")


def test_upload_rejects_other_instance_identity(monkeypatch):
    calls = []

    def request(url, **kwargs):
        calls.append(url)
        return b"synthetic-other-identity"

    monkeypatch.setattr(backup, "request", request)
    with pytest.raises(ValueError):
        backup.upload(b"synthetic", "hourly/synthetic.trlb")
    assert len(calls) == 1


def test_full_cycle_quarantine_and_partial_failure_keep_old_receipt(tmp_path, monkeypatch, keys):
    from radar.ebay_deletion import sync
    from radar.ebay_deletion.store import Queue

    public, private = keys
    secret = b"synthetic-independent-hmac" * 3
    source = Queue(tmp_path / "receiver.db")
    source.bind(secret)
    source.accept("a" * 64, {"userId": "b" * 64})
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    Queue(state / "ledger.db").bind(secret)
    (tmp_path / "hmac").write_bytes(secret)
    (tmp_path / "public.pem").write_bytes(public)
    nonces, objects = [], []

    def pull(nonce):
        nonces.append(nonce)
        return sync.receiver_bundle(source.path, secret, nonce)

    monkeypatch.setattr(sync, "_pull_receiver", pull)
    monkeypatch.setattr(backup, "upload", lambda raw, name: objects.append((name, raw)))
    config = {
        "state_directory": str(state),
        "hmac_file": str(tmp_path / "hmac"),
        "ssh_alias": "synthetic",
        "encryption_public_key": str(tmp_path / "public.pem"),
    }
    report = backup.cycle(config)
    assert report["uploads_verified"] == 3 and report["event_count"] == 1
    assert {name.split("/")[0] for name, _ in objects} == {"hourly", "daily", "weekly"}
    restored = backup.quarantine(objects[0][1], tmp_path / "quarantine", private, secret)
    assert restored["integrity"] == "ok" and restored["current_receiver_proof"] is False
    with pytest.raises(ValueError):
        backup.quarantine(
            objects[0][1], tmp_path / "wrong-key", private, b"wrong-synthetic-hmac" * 3
        )
    # Every subsequent cycle gets a new nonce, even after a recent success.
    backup.cycle(config)
    assert len(nonces) == 2 and len(set(nonces)) == 2
    assert len(objects) == 4
    old_receipt = (state / "success.json").read_bytes()

    def failed_upload(*args):
        raise OSError("synthetic cloud failure")

    monkeypatch.setattr(backup, "upload", failed_upload)
    with pytest.raises(OSError):
        backup.cycle(config)
    assert (state / "success.json").read_bytes() == old_receipt
    # Quarantined backup remains unusable as current proof if the live source fails.
    monkeypatch.setattr(sync, "_pull_receiver", lambda nonce: b"offline saved bundle")
    with pytest.raises(ValueError):
        sync.maintenance_ledger(tmp_path / "quarantine/ledger.db", secret)


def test_quarantine_rejects_archive_links(tmp_path, keys):
    import io
    import tarfile

    public, private = keys
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        link = tarfile.TarInfo("ledger.db")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        archive.addfile(link)
        archive.addfile(tarfile.TarInfo("manifest.json"))
    with pytest.raises(ValueError):
        backup.quarantine(
            backup.encrypt(stream.getvalue(), public),
            tmp_path / "quarantine",
            private,
            b"synthetic" * 8,
        )
    assert not (tmp_path / "quarantine").exists()
