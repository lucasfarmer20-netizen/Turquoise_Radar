"""Synthetic full snapshots and adversarial restores; never require private owner data."""

import hashlib
import json
import zipfile

import pytest

from radar.references.persistence import backup, restore, verify

pytest_plugins = ["tests.test_reference_similarity"]


def test_self_contained_roundtrip_and_existing_tree_preservation(library, tmp_path):
    dest = tmp_path / "snapshot"
    (library / "report-a.txt").write_bytes(b"a" * 300000)
    (library / "report-b.txt").write_bytes(b"b" * 300000)
    result = backup(library, dest, volume_bytes=512 * 1024)
    assert result["volumes"] >= 2
    restored = tmp_path / "restored"
    report = restore(restored, dest, result["manifest_sha256"])
    assert report["statistics"] == verify(library) == verify(restored)
    before = (restored / "references.db").read_bytes()
    with pytest.raises(ValueError, match="overwrite"):
        restore(restored, dest, result["manifest_sha256"])
    assert (restored / "references.db").read_bytes() == before


def test_missing_dataset_never_initializes_an_empty_database(tmp_path):
    absent = tmp_path / "absent"
    with pytest.raises(ValueError, match="Existing"):
        verify(absent)
    with pytest.raises(ValueError, match="Existing"):
        backup(absent, tmp_path / "backup")
    assert not absent.exists()


def test_missing_volume_and_corruption_leave_no_active_replacement(library, tmp_path):
    dest = tmp_path / "backup"
    result = backup(library, dest)
    volume = dest / "part-001.zip"
    volume.write_bytes(b"corrupt")
    restored = tmp_path / "new-root"
    with pytest.raises(ValueError, match="digest"):
        restore(restored, dest, result["manifest_sha256"])
    assert not restored.exists()


def test_manifest_digest_required_and_unsafe_names_rejected(library, tmp_path):
    dest = tmp_path / "backup"
    backup(library, dest)
    with pytest.raises(ValueError, match="digest"):
        restore(tmp_path / "new", dest, "0" * 64)
    path = dest / "backup.json"
    m = json.loads(path.read_text())
    m["files"]["../outside"] = {"bytes": 1, "sha256": hashlib.sha256(b"x").hexdigest()}
    raw = json.dumps(m).encode()
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="Unsafe"):
        restore(tmp_path / "new", dest, hashlib.sha256(raw).hexdigest())
    assert not (tmp_path / "outside").exists()


def test_extra_zip_entry_rejected_even_with_matching_volume_hash(library, tmp_path):
    dest = tmp_path / "backup"
    backup(library, dest)
    volume = dest / "part-001.zip"
    with zipfile.ZipFile(volume, "a") as z:
        z.writestr("unexpected.txt", b"extra")
    path = dest / "backup.json"
    m = json.loads(path.read_text())
    m["volumes"][0]["sha256"] = hashlib.sha256(volume.read_bytes()).hexdigest()
    raw = json.dumps(m).encode()
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="Unexpected"):
        restore(tmp_path / "new", dest, hashlib.sha256(raw).hexdigest())
    assert not (tmp_path / "new").exists()
