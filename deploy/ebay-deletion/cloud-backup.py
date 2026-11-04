"""Private runner backup cycle. Writer never reads or deletes cloud objects.

Requires the system cryptography package, a pre-bound local ledger, an approved
SSH alias, and protected configuration. Recovery uses a separate private RSA key.
No restore operation establishes current receiver proof.
"""

import argparse
import base64
import hashlib
import importlib.util
import io
import json
import os
import secrets
import struct
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from radar.ebay_deletion.sync import maintenance_ledger  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "ledger_backup", Path(__file__).with_name("backup-ledger.py")
)
BACKUP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BACKUP)
MAGIC = b"TRLB1\0"
MAX_BYTES = 64 * 1024 * 1024
MAX_AGE = 90 * 60
IDENTITY = "tr-ledger-runner@turquoiseradar.iam.gserviceaccount.com"
BUCKET = "tr-ebay-ledger-100948930799"


def encrypt(raw, public_pem):
    if len(raw) > MAX_BYTES:
        raise ValueError("Backup exceeds reviewed size bound")
    public = serialization.load_pem_public_key(public_pem)
    if getattr(public, "key_size", None) != 4096:
        raise ValueError("Recovery public key must be RSA 4096")
    key, nonce = AESGCM.generate_key(bit_length=256), secrets.token_bytes(12)
    wrapped = public.encrypt(
        key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
    )
    header = MAGIC + struct.pack(">H", len(wrapped)) + wrapped + nonce
    return header + AESGCM(key).encrypt(nonce, raw, header)


def decrypt(raw, private_pem):
    if len(raw) > MAX_BYTES + 8192 or not raw.startswith(MAGIC):
        raise ValueError("Invalid backup envelope")
    size = struct.unpack(">H", raw[len(MAGIC) : len(MAGIC) + 2])[0]
    end = len(MAGIC) + 2 + size
    if size != 512 or len(raw) < end + 12 + 16:
        raise ValueError("Invalid backup envelope")
    private = serialization.load_pem_private_key(private_pem, password=None)
    key = private.decrypt(
        raw[len(MAGIC) + 2 : end],
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return AESGCM(key).decrypt(raw[end : end + 12], raw[end + 12 :], raw[: end + 12])


def request(url, *, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read(MAX_BYTES + 8193)


def upload(raw, name):
    # Metadata credentials are available only on the approved runner. No key file.
    base = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/"
    headers = {"Metadata-Flavor": "Google"}
    if request(base + "email", headers=headers).decode() != IDENTITY:
        raise ValueError("Unexpected runner identity")
    token = json.loads(request(base + "token", headers=headers))["access_token"]
    query = urllib.parse.urlencode({"uploadType": "media", "name": name, "ifGenerationMatch": "0"})
    result = json.loads(
        request(
            f"https://storage.googleapis.com/upload/storage/v1/b/{BUCKET}/o?{query}",
            data=raw,
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/octet-stream",
            },
        )
    )
    checksum = base64.b64encode(hashlib.md5(raw, usedforsecurity=False).digest()).decode()
    if (
        result.get("md5Hash") != checksum
        or result.get("name") != name
        or result.get("bucket") != BUCKET
        or int(result.get("size", -1)) != len(raw)
        or not str(result.get("generation", "")).isdigit()
    ):
        raise ValueError("Upload receipt verification failed")


def write_receipt(path, value):
    target = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".receipt-", dir=target.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        parent = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def check_age(receipt, now=None):
    now = time.time() if now is None else now
    stamp = json.loads(Path(receipt).read_text())["completed_at"]
    if type(stamp) not in (int, float) or not 0 <= now - stamp <= MAX_AGE:
        raise ValueError("Backup age exceeds accepted threshold")


def cycle(config):
    started = time.monotonic()
    state = Path(config["state_directory"])
    secret = Path(config["hmac_file"]).read_bytes().strip()
    os.environ["EBAY_DELETION_SSH_TARGET"] = config["ssh_alias"]
    # Mandatory fresh nonce-bound proof on EVERY cycle, before any backup/upload.
    maintenance_ledger(state / "ledger.db", secret)
    with tempfile.TemporaryDirectory(prefix="cycle-", dir=state) as scratch:
        directory = Path(scratch) / "verified"
        report = BACKUP.backup_ledger(state / "ledger.db", directory, secret)
        archive = Path(scratch) / "ledger.tar"
        with tarfile.open(archive, "w") as tar:
            for name in ("ledger.db", "manifest.json"):
                tar.add(directory / name, arcname=name, recursive=False)
        if archive.stat().st_size > MAX_BYTES:
            raise ValueError("Backup exceeds reviewed size bound")
        raw = archive.read_bytes()
        encrypted = encrypt(raw, Path(config["encryption_public_key"]).read_bytes())
        now = datetime.now(UTC)
        receipt_path = state / "success.json"
        previous = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        day, week = now.strftime("%Y-%m-%d"), now.strftime("%G-%V")
        tiers = ["hourly"]
        if previous.get("daily") != day:
            tiers.append("daily")
        if previous.get("weekly") != week:
            tiers.append("weekly")
        suffix = now.strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(16) + ".trlb"
        for tier in tiers:
            upload(encrypted, tier + "/" + suffix)
        receipt = {
            "schema": "tr-ledger-success-v1",
            "completed_at": time.time(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "event_count": report["event_count"],
            "receipt_count": report["receipt_count"],
            "uploads_verified": len(tiers),
            "encrypted_bytes": len(encrypted),
            "daily": day,
            "weekly": week,
            "current_receiver_proof": False,
        }
        write_receipt(receipt_path, receipt)
        return receipt


def quarantine(raw, destination, private_pem, secret):
    """Decrypt fixed files into a NEW private quarantine; never import or activate."""
    plaintext = decrypt(raw, private_pem)
    destination = Path(destination)
    with tarfile.open(fileobj=io.BytesIO(plaintext), mode="r:") as archive:
        members = archive.getmembers()
        if (
            len(members) != 2
            or {m.name for m in members} != {"ledger.db", "manifest.json"}
            or any(not m.isfile() or m.size > MAX_BYTES for m in members)
        ):
            raise ValueError("Unexpected recovery archive")
        destination.mkdir(mode=0o700, exist_ok=False)
        for member in members:
            with archive.extractfile(member) as source:
                content = source.read(MAX_BYTES + 1)
            if len(content) != member.size:
                raise ValueError("Invalid recovery member")
            fd = os.open(
                destination / member.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            with os.fdopen(fd, "wb") as target:
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
    return BACKUP.verify_backup(destination, secret)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["cycle", "check-age", "quarantine"])
    parser.add_argument("--config")
    parser.add_argument("--encrypted-file")
    parser.add_argument("--destination")
    parser.add_argument("--private-key")
    parser.add_argument("--hmac-file")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.command == "quarantine":
            if not all([args.encrypted_file, args.destination, args.private_key, args.hmac_file]):
                raise ValueError("Missing recovery argument")
            path = Path(args.encrypted_file)
            if path.stat().st_size > MAX_BYTES + 8192:
                raise ValueError("Oversized recovery input")
            report = quarantine(
                path.read_bytes(),
                args.destination,
                Path(args.private_key).read_bytes(),
                Path(args.hmac_file).read_bytes().strip(),
            )
        else:
            config = json.loads(Path(args.config).read_text())
            if args.command == "cycle":
                report = cycle(config)
            else:
                check_age(Path(config["state_directory"]) / "success.json")
                report = {"backup_age": "within_threshold"}
        print(json.dumps({"status": "ok", **report}, sort_keys=True))
    except Exception:
        # HTTP errors, SSH output and cryptographic errors can contain private data.
        print(json.dumps({"status": "failed", "error": "LEDGER_CLOUD_BACKUP_FAILED"}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
