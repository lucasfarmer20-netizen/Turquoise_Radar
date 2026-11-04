"""OWNER MACHINE ONLY: export via pinned IAP into two encrypted offline copies.

Never run this in the temporary task workspace. Needs owner-controlled gcloud
OS administrator/IAP access, an existing SSH key and Python cryptography >=41.
No plaintext recovery file, key value or passphrase is printed or written.
"""

import argparse
import getpass
import hashlib
import json
import os
import secrets
import shlex
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

PROJECT = "turquoiseradar"
ZONE = "us-west1-b"
HOSTS = {"tr-ebay-notify-01": "8560186650804972514", "tr-ledger-runner-01": "5443443387925882803"}
MAGIC = b"TRKEY1\0"


def derive(passphrase, salt):
    return Scrypt(salt=salt, length=32, n=2**18, r=8, p=1).derive(passphrase.encode())


def seal(raw, passphrase):
    salt, nonce = secrets.token_bytes(32), secrets.token_bytes(12)
    header = MAGIC + salt + nonce
    return header + AESGCM(derive(passphrase, salt)).encrypt(nonce, raw, header)


def open_bundle(raw, passphrase):
    if len(raw) < len(MAGIC) + 60 or not raw.startswith(MAGIC):
        raise ValueError("Invalid recovery envelope")
    end = len(MAGIC) + 44
    return AESGCM(derive(passphrase, raw[len(MAGIC) : len(MAGIC) + 32])).decrypt(
        raw[end - 12 : end], raw[end:], raw[:end]
    )


def gcloud(*args):
    result = subprocess.run(
        ["gcloud", *args, "--project=" + PROJECT, "--quiet"],
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        # Never print CLI/SSH diagnostics; remote stdout may contain recovery keys.
        raise ValueError("Owner IAP export command failed; no captured output displayed")
    return result.stdout


def export(host, code, ssh_key, known_hosts):
    instance_id = (
        gcloud("compute", "instances", "describe", host, "--zone=" + ZONE, "--format=value(id)")
        .decode()
        .strip()
    )
    if instance_id != HOSTS[host]:
        raise ValueError("Instance does not match the deployment receipt")
    console = gcloud(
        "compute", "instances", "get-serial-port-output", host, "--zone=" + ZONE
    ).decode()
    public = {
        line.strip().split()[1]
        for line in console.splitlines()
        if line.strip().startswith("ssh-ed25519 ")
    }
    if len(public) != 1:
        raise ValueError("Authenticated host key missing or ambiguous")
    with known_hosts.open("a") as stream:
        stream.write("compute." + instance_id + " ssh-ed25519 " + public.pop() + "\n")
    command = "sudo -n python3 -I -c " + shlex.quote(code)
    return gcloud(
        "compute",
        "ssh",
        host,
        "--zone=" + ZONE,
        "--tunnel-through-iap",
        "--ssh-key-file=" + str(ssh_key),
        "--strict-host-key-checking=yes",
        "--ssh-flag=-oUserKnownHostsFile=" + str(known_hosts),
        "--ssh-flag=-oGlobalKnownHostsFile=/dev/null",
        "--ssh-flag=-oHostKeyAlgorithms=ssh-ed25519",
        "--command=" + command,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--owner-machine",
        action="store_true",
        required=True,
        help="Confirm execution on the owner-controlled machine",
    )
    parser.add_argument("--ssh-key-file", type=Path, required=True)
    parser.add_argument("--copy-one", type=Path, required=True)
    parser.add_argument("--copy-two", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if not args.ssh_key_file.is_file() or args.copy_one.resolve() == args.copy_two.resolve():
            raise ValueError("Provide an existing SSH key and two different offline destinations")
        if args.copy_one.exists() or args.copy_two.exists():
            raise ValueError("Recovery copies must be new files")
        password = getpass.getpass("New offline recovery passphrase (20+ characters): ")
        if len(password) < 20 or password != getpass.getpass("Confirm passphrase: "):
            raise ValueError("Passphrase confirmation failed")
        hmac_code = """import json
from pathlib import Path
lines=Path('/etc/ebay-receiver/receiver.env').read_text().splitlines()
v=dict(l.split('=',1) for l in lines if l and not l.startswith('#'))
print(json.dumps({'receiver_hmac':v['EBAY_DELETION_HMAC_KEY'],'generation_date':'2026-11-02','generation_time_precision':'day'}))
"""
        private_code = """import json,datetime
from pathlib import Path
p=Path('/root/ledger-recovery/decryption.pem')
print(json.dumps({'backup_decryption_private_pem':p.read_text(),'generated_at':datetime.datetime.fromtimestamp(p.stat().st_mtime,datetime.timezone.utc).isoformat()}))
"""
        with tempfile.TemporaryDirectory(prefix="tr-owner-iap-") as scratch:
            known_hosts = Path(scratch) / "known_hosts"
            hmac_record = json.loads(
                export("tr-ebay-notify-01", hmac_code, args.ssh_key_file, known_hosts)
            )
            encryption_record = json.loads(
                export("tr-ledger-runner-01", private_code, args.ssh_key_file, known_hosts)
            )
        public = (
            serialization.load_pem_private_key(
                encryption_record["backup_decryption_private_pem"].encode(), password=None
            )
            .public_key()
            .public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )
        fingerprints = {
            "hmac_id": hashlib.sha256(hmac_record["receiver_hmac"].encode()).hexdigest()[:16],
            "encryption_public_sha256": hashlib.sha256(public).hexdigest(),
        }
        metadata = {
            "schema": "tr-offline-recovery-v1",
            "project": PROJECT,
            "receiver": "tr-ebay-notify-01",
            "receiver_instance_id": HOSTS["tr-ebay-notify-01"],
            "runner": "tr-ledger-runner-01",
            "bucket": "tr-ebay-ledger-100948930799",
            "exported_at": datetime.now(UTC).isoformat(),
            **fingerprints,
        }
        raw = json.dumps(
            {
                "metadata": metadata,
                "receiver_hmac_record": hmac_record,
                "backup_decryption_record": encryption_record,
            },
            sort_keys=True,
        ).encode()
        encrypted = seal(raw, password)
        for destination in (args.copy_one, args.copy_two):
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            if open_bundle(destination.read_bytes(), password) != raw:
                raise ValueError("Offline copy read/decryption verification failed")
        print(
            json.dumps(
                {
                    "encrypted_copies_verified": 2,
                    **metadata,
                    "physical_separation": "owner must verify placement",
                },
                sort_keys=True,
            )
        )
    except Exception:
        print(json.dumps({"status": "failed", "error": "OWNER_RECOVERY_EXPORT_FAILED"}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
