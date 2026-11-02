"""JRI-012 review regressions: synthetic payloads and disposable package repositories."""

import base64
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from radar.ebay_deletion.protocol import InvalidNotification, event
from radar.ebay_deletion.receiver import create_receiver
from radar.ebay_deletion.store import Queue

SECRET = b"SYNTHETIC-review-secret-000000000000000000"
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("field", ["metadata", "notification", "notification.data"])
@pytest.mark.parametrize("invalid", [None, [], ["SYNTHETIC"], "SYNTHETIC-invalid", 7, True])
def test_nested_invalid_shapes_use_fixed_rejection(tmp_path, field, invalid):
    payload = {
        "metadata": {"topic": "MARKETPLACE_ACCOUNT_DELETION", "schemaVersion": "1.0"},
        "notification": {
            "notificationId": "SYNTHETIC-event",
            "data": {"userId": "SYNTHETIC-account"},
        },
    }
    if field == "notification.data":
        payload["notification"]["data"] = invalid
    else:
        payload[field] = invalid
    body = json.dumps(payload).encode()
    with pytest.raises(InvalidNotification, match="^Unsupported deletion event$"):
        event(body, SECRET)

    async def no_key_lookup(_):
        raise AssertionError("Invalid shape must be rejected before key lookup")

    queue = Queue(tmp_path / "synthetic-queue.db")
    app = create_receiver(
        endpoint="https://notifications.example.invalid/ebay/account-deletion",
        token="SYNTHETIC-verification-00000000000000000",
        secret=SECRET,
        queue=queue,
        keys=no_key_lookup,
    )
    header = base64.b64encode(
        json.dumps(
            {"kid": "SYNTHETIC-key", "signature": base64.b64encode(b"12345678").decode()}
        ).encode()
    ).decode()
    with TestClient(app) as client:
        response = client.post(
            "/ebay/account-deletion",
            content=body,
            headers={"Content-Type": "application/json", "X-EBAY-SIGNATURE": header},
        )
    assert response.status_code == 412
    assert response.json() == {"error": "Invalid notification"}
    assert queue.events() == []


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def package_repo(tmp_path):
    repo = tmp_path / "package-source"
    repo.mkdir()
    # Only tracked source is copied to the disposable fixture. Never copy private working files.
    files = git(
        ROOT,
        "ls-files",
        "radar/ebay_deletion",
        "radar/__init__.py",
        "pyproject.toml",
        "uv.lock",
        "deploy/ebay-deletion",
        "docs/JRI-012.md",
    ).splitlines()
    for name in files:
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    (repo / ".gitignore").write_text(".env\nqueue.db\nprivate-inventory.json\n")
    # Even a tracked file outside the reviewed allowlist must not join the receiver archive.
    (repo / "deploy/ebay-deletion/unreviewed-private.json").write_text("SYNTHETIC-PRIVATE")
    (repo / "radar/main.py").write_text("# SYNTHETIC private workbench\n")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Synthetic Test",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-qm",
        "Synthetic reviewed package",
    )
    return repo, git(repo, "rev-parse", "HEAD")


def test_archive_uses_reviewed_commit_allowlist_and_excludes_private_files(tmp_path):
    repo, reviewed = package_repo(tmp_path)
    for directory in (repo / "deploy/ebay-deletion", repo / "radar/ebay_deletion"):
        for name in (".env", "queue.db", "private-inventory.json"):
            (directory / name).write_text("SYNTHETIC-SECRET-DO-NOT-PACKAGE")
            assert git(repo, "check-ignore", str(directory / name))
    helper = repo / "radar/ebay_deletion/verify.cjs"
    reviewed_helper = helper.read_bytes()
    helper.write_text("SYNTHETIC-UNREVIEWED-HELPER")
    output = tmp_path / "receiver.tar.gz"
    subprocess.run(
        ["sh", str(repo / "deploy/ebay-deletion/package.sh"), str(output), reviewed],
        cwd=repo,
        check=True,
    )
    with tarfile.open(output) as archive:
        names = archive.getnames()
        assert "radar/ebay_deletion/verify.cjs" in names
        assert archive.extractfile("radar/ebay_deletion/verify.cjs").read() == reviewed_helper
        assert "deploy/ebay-deletion/receiver.env.example" in names
        assert "radar/main.py" not in names
        assert "radar/db.py" not in names
        assert "deploy/ebay-deletion/unreviewed-private.json" not in names
        for member in archive.getmembers():
            if member.isfile():
                content = archive.extractfile(member).read()
                assert b"SYNTHETIC-SECRET-DO-NOT-PACKAGE" not in content
                assert b"SYNTHETIC-UNREVIEWED-HELPER" not in content
                assert Path(member.name).name not in (".env", "queue.db", "private-inventory.json")
    # Required files cannot silently disappear from a later review revision.
    helper.write_bytes(reviewed_helper)
    git(repo, "rm", "radar/ebay_deletion/verify.cjs")
    git(
        repo,
        "-c",
        "user.name=Synthetic Test",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-qm",
        "Synthetic missing helper",
    )
    missing_output = tmp_path / "missing-helper.tar.gz"
    result = subprocess.run(
        ["sh", str(repo / "deploy/ebay-deletion/package.sh"), str(missing_output)],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert not missing_output.exists()
