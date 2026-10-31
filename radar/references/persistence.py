"""Self-contained private snapshots; restore never overwrites or initializes a store."""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from PIL import Image

from radar.references.library import local_file
from radar.references.store import exclusive, private_root

SCHEMA = "jri-private-backup-1"
MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_store(root):
    path = Path(root).resolve() / "references.db"
    if not path.is_file():
        raise ValueError("Existing private JRI database required; no empty replacement")
    c = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    if c.execute("PRAGMA user_version").fetchone()[0] != 1:
        c.close()
        raise ValueError("Unsupported reference database")
    return c


def verify(root):
    root = Path(root).resolve()
    with read_store(root) as c:
        if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity failed")
        if c.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Foreign key verification failed")
        media = 0
        for row in c.execute("SELECT * FROM assets WHERE removed=0"):
            data = json.loads(row["data"])
            for key, field in [
                ("original_path", "original_hash"),
                ("normalized_path", "normalized_hash"),
            ]:
                path = local_file(root, data[key])
                if path.stat().st_size > MAX_FILE or digest(path.read_bytes()) != row[field]:
                    raise ValueError("Referenced media integrity failed")
                with Image.open(path) as im:
                    im.verify()
                media += 1
        for row in c.execute("SELECT data FROM crops WHERE removed=0"):
            data = json.loads(row[0])
            path = local_file(root, data["path"])
            if digest(path.read_bytes()) != data["hash"]:
                raise ValueError("Crop integrity failed")
            with Image.open(path) as im:
                im.verify()
        return {
            "sqlite_integrity": "ok",
            "foreign_key_errors": 0,
            "items": c.execute("SELECT count(*) FROM items").fetchone()[0],
            "assets": c.execute("SELECT count(*) FROM assets WHERE removed=0").fetchone()[0],
            "crops": c.execute("SELECT count(*) FROM crops WHERE removed=0").fetchone()[0],
            "verified_media_references": media,
        }


def safe_name(name):
    p = PurePosixPath(name)
    if (
        not name
        or p.is_absolute()
        or ".." in p.parts
        or "\\" in name
        or str(p) != name
        or ":" in name
    ):
        raise ValueError("Unsafe backup path")
    return name


def backup(root, destination, volume_bytes=8 * 1024 * 1024):
    root = Path(root).resolve()
    read_store(root).close()
    destination = Path(destination).resolve()
    if destination.exists() or destination.is_relative_to(root):
        raise ValueError("Backup requires a new private destination outside the active root")
    if not 1024 <= volume_bytes <= 32 * 1024 * 1024:
        raise ValueError("Volume bound must be between 1 KiB and 32 MiB")
    private_root(destination.parent)
    with exclusive(root):
        stats = verify(root)
        stage = Path(tempfile.mkdtemp(prefix=".jri-backup-", dir=destination.parent))
        try:
            snapshot = stage / "snapshot.db"
            with read_store(root) as c, sqlite3.connect(snapshot) as target:
                c.backup(target)
            files, volumes = {}, []
            z = None
            used = 0
            try:
                for path in sorted(root.rglob("*")):
                    if path.is_symlink():
                        raise ValueError("Symlink in private dataset")
                    if not path.is_file() or path.name in {
                        ".writer.lock",
                        "references.db-wal",
                        "references.db-shm",
                        "references.db-journal",
                    }:
                        continue
                    relative = safe_name(path.relative_to(root).as_posix())
                    raw = (
                        snapshot.read_bytes() if relative == "references.db" else path.read_bytes()
                    )
                    if len(raw) > min(MAX_FILE, volume_bytes):
                        raise ValueError("File exceeds volume bound; choose a larger bound")
                    if z is None or used + len(raw) > volume_bytes:
                        if z:
                            z.close()
                        name = f"part-{len(volumes) + 1:03d}.zip"
                        volumes.append(name)
                        z = zipfile.ZipFile(stage / name, "w", zipfile.ZIP_DEFLATED)
                        used = 0
                    z.writestr(relative, raw)
                    files[relative] = {"sha256": digest(raw), "bytes": len(raw)}
                    used += len(raw)
            finally:
                if z:
                    z.close()
            snapshot.unlink()
            manifest = {
                "schema": SCHEMA,
                "files": files,
                "statistics": stats,
                "volumes": [
                    {"name": n, "sha256": digest((stage / n).read_bytes())} for n in volumes
                ],
                "private": True,
                "self_contained": True,
            }
            raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
            (stage / "backup.json").write_bytes(raw)
            for path in stage.iterdir():
                path.chmod(0o600)
            stage.rename(destination)
            return {
                "manifest_sha256": digest(raw),
                "payloads": len(files),
                "volumes": len(volumes),
                "statistics": stats,
            }
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise


def restore(destination, archive_directory, expected_manifest_hash):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError("Refusing to overwrite any existing private tree")
    directory = Path(archive_directory).resolve()
    raw = (directory / "backup.json").read_bytes()
    if len(raw) > 8 * 1024 * 1024 or digest(raw) != expected_manifest_hash:
        raise ValueError("Backup manifest digest mismatch")
    m = json.loads(raw)
    if m["schema"] != SCHEMA or not m.get("self_contained"):
        raise ValueError("Unsupported backup; legacy overlays must first be verified separately")
    files = m["files"]
    if "references.db" not in files or len(files) > 100000:
        raise ValueError("Incomplete backup")
    for name, info in files.items():
        safe_name(name)
        if type(info["bytes"]) is not int or not 0 <= info["bytes"] <= MAX_FILE:
            raise ValueError("Invalid payload bound")
    if sum(info["bytes"] for info in files.values()) > MAX_TOTAL:
        raise ValueError("Backup exceeds restore bound")
    private_root(destination.parent)
    stage = Path(tempfile.mkdtemp(prefix=".jri-restore-", dir=destination.parent))
    seen = set()
    try:
        if not 1 <= len(m["volumes"]) <= 128:
            raise ValueError("Invalid volume count")
        for volume in m["volumes"]:
            name = safe_name(volume["name"])
            if Path(name).name != name:
                raise ValueError("Unsafe volume name")
            path = directory / name
            if path.stat().st_size > MAX_FILE or digest(path.read_bytes()) != volume["sha256"]:
                raise ValueError("Volume digest mismatch")
            with zipfile.ZipFile(path) as z:
                for info in z.infolist():
                    name = safe_name(info.filename)
                    if (
                        name in seen
                        or name not in files
                        or info.is_dir()
                        or (info.external_attr >> 16) & 0o170000 == 0o120000
                        or info.file_size != files[name]["bytes"]
                    ):
                        raise ValueError("Unexpected, duplicate or unsafe payload")
                    with z.open(info) as f:
                        data = f.read(files[name]["bytes"] + 1)
                    if len(data) != files[name]["bytes"] or digest(data) != files[name]["sha256"]:
                        raise ValueError("Payload digest mismatch")
                    target = stage / name
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    target.write_bytes(data)
                    target.chmod(0o600)
                    seen.add(name)
        if seen != set(files):
            raise ValueError("Missing payloads")
        stats = verify(stage)
        if stats != m["statistics"]:
            raise ValueError("Restored database statistics differ")
        stage.rename(destination)
        return {"verified_payloads": len(seen), "statistics": stats}
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path("data/jri"))
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("verify")
    b = sub.add_parser("backup")
    b.add_argument("destination", type=Path)
    b.add_argument("--volume-bytes", type=int, default=8 * 1024 * 1024)
    r = sub.add_parser("restore")
    r.add_argument("archive_directory", type=Path)
    r.add_argument("--manifest-sha256", required=True)
    a = p.parse_args()
    if a.command == "verify":
        result = verify(a.root)
    elif a.command == "backup":
        result = backup(a.root, a.destination, a.volume_bytes)
    else:
        result = restore(a.root, a.archive_directory, a.manifest_sha256)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
