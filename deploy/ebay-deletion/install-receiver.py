"""Prepare a NEW Ubuntu 24.04 receiver host; changes require explicit --apply.

Run from an extracted, reviewed receiver archive after separate deployment approval.
Requires Python 3.12+, Node 22/24 LTS, uv, Caddy, OpenSSH and sudo already installed.
Never provisions GCE, opens a firewall, starts a service, or contacts eBay. Applying
installs locked runtime wheels from PyPI; configuration/SSH activation remains a
separate operator step after validating and reviewing the written configuration.
"""

import argparse
import base64
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

RUNTIME_PATH = "/usr/local/bin:/usr/bin:/bin"

RUNTIME_FILES = (
    "radar/__init__.py",
    "radar/ebay_deletion/__init__.py",
    "radar/ebay_deletion/protocol.py",
    "radar/ebay_deletion/receiver.py",
    "radar/ebay_deletion/store.py",
    "radar/ebay_deletion/sync.py",
    "radar/ebay_deletion/verify.cjs",
    "deploy/ebay-deletion/snapshot-queue.py",
    "deploy/ebay-deletion/backup-ledger.py",
)
CONFIG_KEYS = {
    "EBAY_DELETION_ENDPOINT",
    "EBAY_DELETION_VERIFICATION_TOKEN",
    "EBAY_DELETION_HMAC_KEY",
    "EBAY_DELETION_QUEUE",
    "EBAY_NOTIFICATION_CLIENT_ID",
    "EBAY_NOTIFICATION_CLIENT_SECRET",
}


def configuration(path):
    """Accept the intentionally small, unquoted systemd environment-file format."""
    values = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if (
            not sep
            or key not in CONFIG_KEYS
            or key in values
            or not re.fullmatch(r"[A-Za-z0-9_./:@%+?,=~-]+", value)
            or "REPLACE" in value
        ):
            raise ValueError("Invalid receiver configuration")
        values[key] = value
    if values.keys() != CONFIG_KEYS:
        raise ValueError("Missing receiver configuration")
    url = urlsplit(values["EBAY_DELETION_ENDPOINT"])
    if (
        url.scheme != "https"
        or not url.hostname
        or url.netloc != url.hostname
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", url.hostname)
        or url.hostname.endswith((".invalid", ".example", ".localhost"))
        or "." not in url.hostname
        or url.path != "/ebay/account-deletion"
        or url.query
        or url.fragment
        or values["EBAY_DELETION_QUEUE"] != "/var/lib/ebay-receiver/queue.db"
        or not re.fullmatch(r"[A-Za-z0-9_-]{32,80}", values["EBAY_DELETION_VERIFICATION_TOKEN"])
        or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", values["EBAY_DELETION_HMAC_KEY"])
        or values["EBAY_DELETION_VERIFICATION_TOKEN"] == values["EBAY_DELETION_HMAC_KEY"]
    ):
        raise ValueError("Invalid receiver configuration")
    return values


def ledger_key(path):
    fields = path.read_text().strip().split()
    if len(fields) < 2 or fields[0] != "ssh-ed25519" or "\n" in path.read_text().strip():
        raise ValueError("Supply one dedicated plain Ed25519 public key")
    try:
        raw = base64.b64decode(fields[1], validate=True)
    except ValueError:
        raise ValueError("Invalid ledger public key") from None
    if len(raw) != 51 or raw[:19] != b"\0\0\0\x0bssh-ed25519\0\0\0\x20":
        raise ValueError("Invalid ledger public key")
    return "restrict " + " ".join(fields[:2]) + " ebay-ledger-pull\n"


def runtime_requirements(lock):
    """Select only the receiver's dependency closure from the reviewed uv lock."""
    packages = {package["name"]: package for package in tomllib.loads(lock.read_text())["package"]}
    pending, selected = ["fastapi", "httpx", "uvicorn"], set()
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        package = packages[name]
        if package.get("source") != {"registry": "https://pypi.org/simple"}:
            raise ValueError("Unexpected runtime package source")
        selected.add(name)
        pending.extend(item["name"] for item in package.get("dependencies", []))
    return "".join(
        package["name"]
        + "=="
        + package["version"]
        + " "
        + " ".join("--hash=" + wheel["hash"] for wheel in package["wheels"])
        + "\n"
        for package in (packages[name] for name in sorted(selected))
    )


def run(*args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def preflight(source, config, public_key):
    values, key = configuration(config), ledger_key(public_key)
    requirements = runtime_requirements(source / "uv.lock")
    for name in RUNTIME_FILES + (
        "deploy/ebay-deletion/receiver.service",
        "deploy/ebay-deletion/Caddyfile",
        "deploy/ebay-deletion/sshd-ledger.conf",
        "deploy/ebay-deletion/sudoers-ledger",
    ):
        if (
            not stat.S_ISREG((source / name).lstat().st_mode)
            or (source / name).resolve() != source.resolve() / name
        ):
            raise ValueError("Receiver archive has missing or nonregular files")
    return values, key, requirements


def install(source, values, key, requirements, root=Path("/")):
    """New-host installation only. Root is injectable solely for disposable tests."""
    target = root / "opt/ebay-receiver"
    managed = (
        target,
        root / "etc/ebay-receiver",
        root / "var/lib/ebay-receiver",
        root / "etc/ssh/sshd_config.d/00-ebay-ledger.conf",
        root / "etc/ssh/authorized_keys/ebay-ledger-pull",
        root / "etc/sudoers.d/ebay-ledger-pull",
        root / "etc/systemd/system/ebay-receiver.service",
    )
    if any(path.exists() or path.is_symlink() for path in managed):
        raise ValueError("Existing receiver installation requires a separately reviewed update")
    run(
        "useradd",
        "--system",
        "--user-group",
        "--no-create-home",
        "--home-dir",
        "/var/lib/ebay-receiver",
        "--shell",
        "/usr/sbin/nologin",
        "ebay-receiver",
    )
    run(
        "useradd",
        "--system",
        "--user-group",
        "--no-create-home",
        "--home-dir",
        "/var/empty",
        "--shell",
        "/bin/sh",
        "ebay-ledger-pull",
    )
    # SSH needs a valid shell for ForceCommand; password authentication is disabled.
    # Set a deliberately non-hash password so PAM permits public-key login, never a password.
    run("usermod", "--password", "*", "ebay-ledger-pull")
    account = pwd.getpwnam("ebay-receiver")

    def write(path, content, mode=0o644, gid=0):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_relative_to(target):
            directory = path.parent
            while directory != target.parent:
                directory.chmod(0o755)
                directory = directory.parent
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            os.chown(path, 0, gid)
            stream.write(content if isinstance(content, bytes) else content.encode())

    for name in RUNTIME_FILES:
        write(target / name, (source / name).read_bytes())
    write(target / "receiver-requirements.txt", requirements)
    write(
        root / "etc/ebay-receiver/receiver.env",
        "".join(f"{name}={values[name]}\n" for name in sorted(values)),
        0o640,
        account.pw_gid,
    )
    (root / "etc/ebay-receiver").chmod(0o750)
    os.chown(root / "etc/ebay-receiver", 0, account.pw_gid)
    directory = root / "var/lib/ebay-receiver"
    directory.mkdir(parents=True, mode=0o700)
    os.chown(directory, account.pw_uid, account.pw_gid)
    write(root / "etc/ssh/authorized_keys/ebay-ledger-pull", key, 0o644)
    (root / "etc/ssh/authorized_keys").chmod(0o755)
    templates = {
        "receiver.service": "etc/systemd/system/ebay-receiver.service",
        "sshd-ledger.conf": "etc/ssh/sshd_config.d/00-ebay-ledger.conf",
        "sudoers-ledger": "etc/sudoers.d/ebay-ledger-pull",
    }
    for name, destination in templates.items():
        write(
            root / destination,
            (source / "deploy/ebay-deletion" / name).read_bytes(),
            0o440 if name == "sudoers-ledger" else 0o644,
        )
    # Preserve the distributor's Caddyfile until the separate activation step.
    caddy = (source / "deploy/ebay-deletion/Caddyfile").read_text()
    write(
        root / "etc/ebay-receiver/Caddyfile",
        caddy.replace(
            "ebay-notify.example.invalid", urlsplit(values["EBAY_DELETION_ENDPOINT"]).hostname
        ),
    )
    run(sys.executable, "-m", "venv", "--without-pip", str(target / ".venv"))
    run(
        "uv",
        "pip",
        "install",
        "--python",
        str(target / ".venv/bin/python"),
        "--require-hashes",
        "--no-deps",
        "--only-binary=:all:",
        "--index-url",
        "https://pypi.org/simple",
        "-r",
        str(target / "receiver-requirements.txt"),
    )
    run("visudo", "-cf", str(root / "etc/sudoers.d/ebay-ledger-pull"))
    run("sshd", "-t")
    run("systemd-analyze", "verify", str(root / "etc/systemd/system/ebay-receiver.service"))
    run(
        "caddy",
        "validate",
        "--adapter",
        "caddyfile",
        "--config",
        str(root / "etc/ebay-receiver/Caddyfile"),
    )


def validate_runtime_node():
    """Check the Node executable visible to the service, not an admin's nvm PATH."""
    node = shutil.which("node", path=RUNTIME_PATH)
    if not node:
        raise ValueError("Node is missing from the receiver runtime PATH")
    version = subprocess.check_output(
        [node, "--version"], text=True, env={**os.environ, "PATH": RUNTIME_PATH}
    ).strip()
    if not re.fullmatch(r"v\d+\.\d+\.\d+", version) or int(version[1:].split(".")[0]) not in (
        22,
        24,
    ):
        raise ValueError("Node 22/24 LTS is required in the receiver runtime PATH")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--ledger-public-key", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        source = Path(__file__).resolve().parents[2]
        prepared = preflight(source, args.config, args.ledger_public_key)
        if not args.apply:
            print(
                "Configuration validated; no changes. Installation requires --apply after approval."
            )
            return
        if os.geteuid() != 0 or sys.version_info < (3, 12):
            raise ValueError("Installation requires root and Python 3.12+")
        release = Path("/etc/os-release").read_text()
        if "ID=ubuntu\n" not in release or 'VERSION_ID="24.04"' not in release:
            raise ValueError("Reviewed host image is Ubuntu 24.04 LTS")
        for binary in ("uv", "caddy", "sshd", "visudo", "systemd-analyze"):
            if not shutil.which(binary):
                raise ValueError("Required runtime or host tool missing")
        validate_runtime_node()
        os.umask(0o022)
        install(source, *prepared)
        print("Installed and validated; services and SSH were NOT activated or reloaded.")
        print(
            "Review files, install the prepared Caddyfile, then explicitly activate services/SSH."
        )
    except (ValueError, OSError, KeyError, subprocess.SubprocessError):
        print("Receiver preparation failed; no service activation attempted", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
