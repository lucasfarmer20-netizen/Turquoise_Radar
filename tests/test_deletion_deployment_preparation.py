"""Deployment preparation checks use synthetic configuration and disposable host trees."""

import base64
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from radar.ebay_deletion.store import Queue

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy/ebay-deletion"
SECRET = "SYNTHETIC-receiver-secret-0000000000000000000000000"


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), DEPLOY / f"{name}.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def synthetic_config(tmp_path):
    config = tmp_path / "synthetic.env"
    config.write_text(
        "EBAY_DELETION_ENDPOINT=https://synthetic.example.com/ebay/account-deletion\n"
        "EBAY_DELETION_VERIFICATION_TOKEN=SYNTHETIC-verification-000000000000000000\n"
        f"EBAY_DELETION_HMAC_KEY={SECRET}\n"
        "EBAY_DELETION_QUEUE=/var/lib/ebay-receiver/queue.db\n"
        "EBAY_NOTIFICATION_CLIENT_ID=SYNTHETIC-client-id\n"
        "EBAY_NOTIFICATION_CLIENT_SECRET=SYNTHETIC-client-secret\n"
    )
    config.chmod(0o640)
    public_key = tmp_path / "synthetic.pub"
    wire = b"\0\0\0\x0bssh-ed25519\0\0\0\x20" + b"S" * 32
    public_key.write_text("ssh-ed25519 " + base64.b64encode(wire).decode() + " SYNTHETIC\n")
    return config, public_key


def test_default_validates_without_mutation_or_subprocess(tmp_path, monkeypatch, capsys):
    installer = module("install-receiver")
    config, key = synthetic_config(tmp_path)
    monkeypatch.setattr(
        sys, "argv", ["install", "--config", str(config), "--ledger-public-key", str(key)]
    )
    monkeypatch.setattr(installer, "install", lambda *a: pytest.fail("Unexpected host mutation"))
    monkeypatch.setattr(installer, "run", lambda *a: pytest.fail("Unexpected host command"))
    installer.main()
    assert "no changes" in capsys.readouterr().out


def test_new_host_install_is_receiver_only_and_does_not_activate(tmp_path, monkeypatch):
    installer = module("install-receiver")
    config, public_key = synthetic_config(tmp_path)
    prepared = installer.preflight(ROOT, config, public_key)
    host = tmp_path / "host"
    calls, owners = [], []
    monkeypatch.setattr(installer, "run", lambda *a, **kw: calls.append(a))
    monkeypatch.setattr(
        installer.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=991, pw_gid=991)
    )
    monkeypatch.setattr(installer.os, "chown", lambda *a: owners.append(a))
    installer.install(ROOT, *prepared, root=host)
    target = host / "opt/ebay-receiver"
    installed = {str(path.relative_to(target)) for path in target.rglob("*") if path.is_file()}
    assert installed == set(installer.RUNTIME_FILES) | {"receiver-requirements.txt"}
    assert "radar/ebay_deletion/verify.cjs" in installed
    assert not any(name in installed for name in ("radar/main.py", "radar/db.py", "queue.db"))
    assert not (host / "var/lib/ebay-receiver/queue.db").exists()
    assert (host / "etc/ebay-receiver/receiver.env").stat().st_mode & 0o777 == 0o640
    assert (host / "etc/ebay-receiver").stat().st_mode & 0o777 == 0o750
    assert (host / "var/lib/ebay-receiver").stat().st_mode & 0o777 == 0o700
    assert (host / "etc/sudoers.d/ebay-ledger-pull").stat().st_mode & 0o777 == 0o440
    assert (host / "etc/ssh/authorized_keys/ebay-ledger-pull").stat().st_mode & 0o777 == 0o644
    assert (
        (host / "etc/ssh/authorized_keys/ebay-ledger-pull")
        .read_text()
        .startswith("restrict ssh-ed25519 ")
    )
    assert not any(call[0] in ("systemctl", "gcloud", "curl", "ssh") for call in calls)
    pip = next(call for call in calls if call[:3] == ("uv", "pip", "install"))
    assert "--require-hashes" in pip and "--no-deps" in pip and "--only-binary=:all:" in pip
    requirements = (target / "receiver-requirements.txt").read_text()
    assert "fastapi==0.119.1 --hash=sha256:" in requirements
    assert "pydantic-core==" in requirements
    assert not any(name in requirements for name in ("anthropic", "pillow", "sqlalchemy", "torch"))
    assert not (host / "etc/caddy/Caddyfile").exists()
    assert "synthetic.example.com" in (host / "etc/ebay-receiver/Caddyfile").read_text()
    with pytest.raises(ValueError, match="Existing receiver"):
        installer.install(ROOT, *prepared, root=host)


@pytest.mark.parametrize("change", ["placeholder", "shell", "same_key", "path", "duplicate"])
def test_unsafe_configuration_rejected_before_apply(tmp_path, change):
    installer = module("install-receiver")
    config, key = synthetic_config(tmp_path)
    contents = config.read_text()
    if change == "placeholder":
        contents = contents.replace("synthetic.example.com", "ebay-notify.example.invalid")
    elif change == "shell":
        contents = contents.replace("SYNTHETIC-client-secret", "$(touch /tmp/forbidden)")
    elif change == "same_key":
        contents = contents.replace("SYNTHETIC-verification-000000000000000000", SECRET)
    elif change == "path":
        contents = contents.replace("/var/lib/ebay-receiver/queue.db", "/private-owner.db")
    else:
        contents += f"EBAY_DELETION_HMAC_KEY={SECRET}\n"
    config.write_text(contents)
    with pytest.raises(ValueError, match="configuration"):
        installer.preflight(ROOT, config, key)


def test_forced_snapshot_uses_protected_key_and_existing_current_queue(tmp_path, monkeypatch):
    snapshot = module("snapshot-queue")
    config, _ = synthetic_config(tmp_path)
    # Unit tests assert ownership checks separately; synthetic owner need not be root.
    monkeypatch.setattr(snapshot, "protected_secret", lambda _: SECRET.encode())
    monkeypatch.setattr(snapshot, "CONFIG", config)
    monkeypatch.setattr(snapshot, "CODE", ROOT)
    monkeypatch.setattr(snapshot, "QUEUE", tmp_path / "queue.db")
    monkeypatch.setattr(sys, "argv", ["snapshot-queue.py"])
    monkeypatch.delenv("SSH_ORIGINAL_COMMAND", raising=False)
    monkeypatch.setenv("EBAY_DELETION_HMAC_KEY", "UNTRUSTED-CALLER-KEY")
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"a" * 64 + b"\n")))
    output = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=output))
    with pytest.raises(SystemExit) as error:
        snapshot.main()
    assert error.value.code == 2
    assert not snapshot.QUEUE.exists()
    queue = Queue(snapshot.QUEUE)
    queue.bind(SECRET.encode())
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"b" * 64 + b"\n")))
    snapshot.main()
    first = json.loads(output.getvalue())
    assert first["payload"]["events"] == {}
    queue.accept("c" * 64, {"userId": "d" * 64})
    output.seek(0)
    output.truncate()
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"e" * 64 + b"\n")))
    snapshot.main()
    second = json.loads(output.getvalue())
    assert second["payload"]["nonce"] == "e" * 64
    assert list(second["payload"]["events"]) == ["c" * 64]


@pytest.mark.parametrize("wire", [b"bad\n", b"a" * 64, b"a" * 64 + b"\r\n", b"a" * 65 + b"\n"])
def test_forced_snapshot_rejects_bad_nonce_before_reading_secrets(wire, monkeypatch):
    snapshot = module("snapshot-queue")
    monkeypatch.setattr(sys, "argv", ["snapshot-queue.py"])
    monkeypatch.delenv("SSH_ORIGINAL_COMMAND", raising=False)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(wire)))
    monkeypatch.setattr(snapshot, "protected_secret", lambda _: pytest.fail("Secret read"))
    with pytest.raises(SystemExit) as error:
        snapshot.main()
    assert error.value.code == 2


def test_secret_file_and_ssh_boundaries(tmp_path):
    snapshot = module("snapshot-queue")
    config, _ = synthetic_config(tmp_path)
    if os.geteuid() == 0:
        assert snapshot.protected_secret(config) == SECRET.encode()
    else:
        with pytest.raises(ValueError, match="Unsafe"):
            snapshot.protected_secret(config)
    config.chmod(0o644)
    with pytest.raises(ValueError, match="Unsafe"):
        snapshot.protected_secret(config)
    link = tmp_path / "linked.env"
    link.symlink_to(config)
    with pytest.raises(ValueError, match="Unsafe"):
        snapshot.protected_secret(link)
    server = (DEPLOY / "sshd-ledger.conf").read_text()
    assert "Match User ebay-ledger-pull" in server
    assert "ForceCommand /usr/bin/sudo -n -u ebay-receiver" in server
    assert "DisableForwarding yes" in server and "PermitTTY no" in server
    assert "AuthorizedKeysCommand none" in server
    sudoers = (DEPLOY / "sudoers-ledger").read_text()
    assert "ALL=(ebay-receiver) NOPASSWD:" in sudoers
    assert "*" not in sudoers
    client = (DEPLOY / "ssh-client.conf.example").read_text()
    assert "StrictHostKeyChecking yes" in client
    assert "GlobalKnownHostsFile /dev/null" in client
    assert "UserKnownHostsFile ~/.ssh/ebay-deletion-ledger_known_hosts" in client
    assert "HostKeyAlias ebay-deletion-ledger-RECEIVER_INSTANCE_ID" in client
    assert "IdentityAgent none" in client and "UpdateHostKeys no" in client
    assert "start-iap-tunnel" in client


def test_ssh_client_configuration_parses(tmp_path):
    result = subprocess.run(
        ["ssh", "-G", "-F", str(DEPLOY / "ssh-client.conf.example"), "ebay-deletion-ledger"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "user ebay-ledger-pull\n" in result.stdout
    assert "stricthostkeychecking true\n" in result.stdout
    assert "identityagent none\n" in result.stdout


def test_sshd_matches_only_the_restricted_account(tmp_path):
    sshd = shutil.which("sshd")
    if not sshd:
        pytest.skip("OpenSSH server parser is not installed")
    hostkey = tmp_path / "synthetic_host_ed25519"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(hostkey)], check=True)
    config = tmp_path / "sshd_config"
    config.write_text(f"HostKey {hostkey}\n" + (DEPLOY / "sshd-ledger.conf").read_text())
    for user in ("ebay-ledger-pull", "synthetic-admin"):
        result = subprocess.run(
            [sshd, "-T", "-f", str(config), "-C", f"user={user},host=localhost,addr=127.0.0.1"],
            capture_output=True,
            text=True,
            check=True,
        )
        if user == "ebay-ledger-pull":
            assert "passwordauthentication no\n" in result.stdout
            assert "permittty no\n" in result.stdout
            assert "disableforwarding yes\n" in result.stdout
            assert "forcecommand /usr/bin/sudo -n -u ebay-receiver " in result.stdout
        else:
            assert "forcecommand none\n" in result.stdout
            assert "disableforwarding no\n" in result.stdout


@pytest.mark.parametrize("runtime_version", [None, "v18.20.0", "v24.19.0"])
def test_node_preflight_uses_service_path_not_admin_path(tmp_path, monkeypatch, runtime_version):
    installer = module("install-receiver")
    assert (
        f"Environment=PATH={installer.RUNTIME_PATH}\n" in (DEPLOY / "receiver.service").read_text()
    )
    admin, runtime = tmp_path / "admin-nvm", tmp_path / "system-bin"
    admin.mkdir()
    runtime.mkdir()
    admin_node = admin / "node"
    admin_node.write_text("#!/bin/sh\nprintf 'v24.19.0\\n'\n")
    admin_node.chmod(0o755)
    monkeypatch.setenv("PATH", str(admin))
    monkeypatch.setattr(installer, "RUNTIME_PATH", str(runtime))
    assert shutil.which("node") == str(admin_node)
    if runtime_version:
        runtime_node = runtime / "node"
        runtime_node.write_text(f"#!/bin/sh\nprintf '{runtime_version}\\n'\n")
        runtime_node.chmod(0o755)
    if runtime_version == "v24.19.0":
        installer.validate_runtime_node()
    else:
        with pytest.raises(ValueError, match="runtime PATH"):
            installer.validate_runtime_node()
