"""OWNER MACHINE ONLY: time a restore-reader download through protected quarantine.

Unlock an approved offline recovery copy only in owner-process memory. Download
the fixed, previously qualified synthetic object using the separate restore
reader. This never installs a recovered ledger or establishes current proof.
Do not run against production recovery material in Codex or Cloud Shell.
"""

import argparse
import getpass
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXPORT = load("owner_recovery_export", "export-recovery.py")
CLOUD = load("owner_recovery_cloud_backup", "cloud-backup.py")
OWNER = "lucas.farmer20@gmail.com"
READER = "tr-ledger-restore-reader@turquoiseradar.iam.gserviceaccount.com"
SOURCE = (
    "gs://tr-ebay-ledger-100948930799/synthetic/"
    "recovery-qualification-20261102T192436Z-e5a0d02620faa2943ce04f98032c8909.trlb"
)
CIPHERTEXT_SHA256 = "721b3c7b5356093034f8260c88d8e511e385f5d5bd2a5a9842e2ab1bb9c71170"
HMAC_ID = "0eb6b203e7fe4ffd"
PUBLIC_SHA256 = "042d38760c4e132e24a072539d8abfc52f90cf37d6e1ef019fd1a85459524e40"
MAX_RECOVERY_COPY = 1024 * 1024


def utc_now():
    return datetime.now(UTC).isoformat()


def owner_context():
    # Explicit CLI acknowledgement is also required. Reject common managed
    # environments and token overrides rather than silently changing identity.
    forbidden = (
        "OIC_MANIFEST_PATH",
        "CLOUD_SHELL",
        "DEVSHELL_PROJECT_ID",
        "CLOUDSDK_AUTH_ACCESS_TOKEN",
        "CLOUDSDK_AUTH_ACCESS_TOKEN_FILE",
        "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE",
        "CLOUDSDK_AUTH_LOGIN_CONFIG_FILE",
    )
    if any(os.environ.get(name) for name in forbidden):
        raise ValueError("Use the owner-controlled user login")


def private_bytes(path, maximum):
    with CLOUD.BACKUP.private_parent(path) as (parent, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) & 0o077
                or info.st_uid != os.getuid()
                or info.st_size > maximum
            ):
                raise ValueError("Require a bounded private regular file")
            content = stream.read(maximum + 1)
            if len(content) > maximum:
                raise ValueError("Oversized private file")
            return content


def new_private_directory(path):
    path = Path(os.path.abspath(path))
    with CLOUD.BACKUP.private_parent(path) as (parent, name):
        info = os.fstat(parent)
        if stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid != os.getuid():
            raise ValueError("Use an existing owner-private parent directory")
        os.mkdir(name, mode=0o700, dir_fd=parent)
        os.fsync(parent)
    return path


def unlock(recovery_copy):
    encrypted = private_bytes(recovery_copy, MAX_RECOVERY_COPY)
    password = getpass.getpass("Offline recovery passphrase (hidden): ")
    bundle = json.loads(EXPORT.open_bundle(encrypted, password))
    secret = bundle["receiver_hmac_record"]["receiver_hmac"].encode()
    private_pem = bundle["backup_decryption_record"]["backup_decryption_private_pem"].encode()
    private = serialization.load_pem_private_key(private_pem, password=None)
    public = private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    if (
        len(secret) < 32
        or getattr(private, "key_size", None) != 4096
        or hashlib.sha256(secret).hexdigest()[:16] != HMAC_ID
        or hashlib.sha256(public).hexdigest() != PUBLIC_SHA256
    ):
        raise ValueError("Recovery credentials do not match the approved receipt")
    return private_pem, secret


def download(destination):
    env = os.environ.copy()
    env.update(
        CLOUDSDK_CORE_DISABLE_FILE_LOGGING="true",
        CLOUDSDK_CORE_LOG_HTTP="false",
        CLOUDSDK_CORE_VERBOSITY="error",
    )
    result = subprocess.run(
        [
            "gcloud",
            "storage",
            "cp",
            "--no-clobber",
            SOURCE,
            str(destination),
            "--account=" + OWNER,
            "--impersonate-service-account=" + READER,
            "--project=turquoiseradar",
            "--quiet",
            "--verbosity=error",
        ],
        env=env,
        capture_output=True,
        timeout=4 * 60 * 60,
        check=False,
    )
    if result.returncode:
        raise ValueError("Restore-reader download failed")


def timed_recovery(recovery_copy, destination):
    owner_context()
    private_pem, secret = unlock(recovery_copy)
    directory = new_private_directory(destination)
    encrypted_file = directory / "download.trlb"
    started_utc = utc_now()
    started = time.monotonic()
    download(encrypted_file)
    raw = private_bytes(encrypted_file, CLOUD.MAX_BYTES + 8192)
    if hashlib.sha256(raw).hexdigest() != CIPHERTEXT_SHA256:
        raise ValueError("Downloaded object does not match the qualified synthetic object")
    report = CLOUD.quarantine(raw, directory / "quarantine", private_pem, secret)
    if (
        report.get("event_count") != 1
        or report.get("receipt_count") != 0
        or report.get("offline_copy_only") is not True
        or report.get("current_receiver_proof") is not False
    ):
        raise ValueError("Unexpected synthetic recovery result")
    completed_utc = utc_now()
    elapsed = time.monotonic() - started
    if elapsed < 0 or datetime.fromisoformat(completed_utc) < datetime.fromisoformat(started_utc):
        raise ValueError("Recovery timing clock moved backwards")
    return {
        "status": "ok",
        "measurement_scope": "restore_reader_download_through_protected_quarantine",
        "started_utc": started_utc,
        "completed_utc": completed_utc,
        "duration_seconds": round(elapsed, 3),
        "offline_bundle_unlock_included": False,
        "synthetic_object_checksum_verified": True,
        "restore_reader_download_verified": True,
        "event_count": report["event_count"],
        "receipt_count": report["receipt_count"],
        "integrity": report["integrity"],
        "key_bound": report["key_bound"],
        "offline_copy_only": True,
        "current_receiver_proof": False,
        "operational_restore_performed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-machine", action="store_true", required=True)
    parser.add_argument("--recovery-copy", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            raise ValueError("Use an interactive owner terminal for hidden passphrase input")
        report = timed_recovery(args.recovery_copy, args.destination)
        print(json.dumps(report, sort_keys=True))
    except Exception:
        # gcloud output and decryption errors can contain private data. A failed
        # or expired source never becomes a successful or partial RTO claim.
        print(json.dumps({"status": "failed", "error": "OWNER_TIMED_RECOVERY_FAILED"}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
