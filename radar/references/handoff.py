"""Self-contained private JRI + directory + evaluation handoff, never an overlay restore."""

import argparse
import json
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from radar.references.directory import stats
from radar.references.persistence import MAX_FILE, MAX_TOTAL, digest, safe_name, verify
from radar.references.store import dump, private_root

SCHEMA = "jri-research-handoff-1"
COMPONENTS = {"jri", "directory", "evaluation"}


def check(root):
    from radar.references.books import verify as verify_books

    root = Path(root)
    evaluation = root / "evaluation" / "package"
    manifest = json.loads((evaluation / "manifest.json").read_text())
    if len(manifest["items"]) != manifest["image_count"]:
        raise ValueError("Evaluation manifest count mismatch")
    for entry in manifest["items"]:
        name = safe_name(entry["file"])
        raw = (evaluation / name).read_bytes()
        if digest(raw) != entry["sha256"] or len(raw) != entry["bytes"]:
            raise ValueError("Original evaluation photograph integrity failed")
    return {
        "library": verify(root / "jri"),
        "directory": stats(root / "directory"),
        "book_evidence": verify_books(root / "directory"),
        "evaluation_originals": len(manifest["items"]),
    }


def backup(destination, library, directory, evaluation, *, volume_bytes=8 * 1024 * 1024):
    verify(library)
    stats(directory)
    destination = Path(destination).resolve()
    roots = {
        "jri": Path(library).resolve(),
        "directory": Path(directory).resolve(),
        "evaluation": Path(evaluation).resolve(),
    }
    if destination.exists() or any(destination.is_relative_to(p) for p in roots.values()):
        raise ValueError("New backup destination outside source components required")
    if not 1024 <= volume_bytes <= 32 * 1024 * 1024:
        raise ValueError("Invalid volume bound")
    private_root(destination.parent)
    stage = Path(tempfile.mkdtemp(prefix=".handoff-", dir=destination.parent))
    try:
        snapshot = stage / "snapshot"
        snapshot.mkdir()
        for name, root in roots.items():
            if not root.is_dir():
                raise ValueError("Missing private component")
            for p in root.rglob("*"):
                if p.is_symlink():
                    raise ValueError("Symlink in private component")
                if (
                    not p.is_file()
                    or p.name == ".writer.lock"
                    or p.name.endswith((".db-wal", ".db-shm", ".db-journal"))
                ):
                    continue
                target = snapshot / name / p.relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                if p.name in {"references.db", "directory.db"}:
                    with sqlite3.connect(f"file:{p}?mode=ro", uri=True) as source:
                        with sqlite3.connect(target) as out:
                            source.backup(out)
                else:
                    shutil.copyfile(p, target)
        check(snapshot)
        files, volumes, total, used, z = {}, [], 0, 0, None
        try:
            for p in sorted(snapshot.rglob("*")):
                if not p.is_file():
                    continue
                raw = p.read_bytes()
                total += len(raw)
                if len(raw) > min(MAX_FILE, volume_bytes) or total > MAX_TOTAL:
                    raise ValueError("Snapshot bound exceeded; select a larger volume bound")
                if z is None or used + len(raw) > volume_bytes:
                    if z:
                        z.close()
                    filename = f"part-{len(volumes) + 1:03d}.zip"
                    volumes.append(filename)
                    z = zipfile.ZipFile(stage / filename, "w", zipfile.ZIP_DEFLATED)
                    used = 0
                name = safe_name(p.relative_to(snapshot).as_posix())
                z.writestr(name, raw)
                files[name] = {"sha256": digest(raw), "bytes": len(raw)}
                used += len(raw)
        finally:
            if z:
                z.close()
        manifest = {
            "schema": SCHEMA,
            "private": True,
            "self_contained": True,
            "files": files,
            "volumes": [{"name": n, "sha256": digest((stage / n).read_bytes())} for n in volumes],
            "checks": check(snapshot),
            "model": (
                "ResNet-18 weights remain separate pinned public weights; no implicit download."
            ),
        }
        raw = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
        (stage / "backup.json").write_bytes(raw)
        shutil.rmtree(snapshot)
        for p in stage.iterdir():
            p.chmod(0o600)
        stage.rename(destination)
        return {"manifest_sha256": digest(raw), "payloads": len(files), "volumes": len(volumes)}
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def restore(backup_root, destination, manifest_sha256):
    source, destination = Path(backup_root).resolve(), Path(destination).resolve()
    if destination.exists():
        raise ValueError("Refusing to overwrite an existing private tree")
    raw = (source / "backup.json").read_bytes()
    if len(raw) > 32 * 1024 * 1024 or digest(raw) != manifest_sha256:
        raise ValueError("Manifest digest mismatch")
    m = json.loads(raw)
    if m.get("schema") != SCHEMA or not m.get("self_contained"):
        raise ValueError("Unsupported handoff")
    if not m["files"] or len(m["files"]) > 100000:
        raise ValueError("Invalid payload count")
    for name, info in m["files"].items():
        safe_name(name)
        if name.split("/")[0] not in COMPONENTS or not 0 <= info["bytes"] <= MAX_FILE:
            raise ValueError("Unsafe payload")
    if sum(i["bytes"] for i in m["files"].values()) > MAX_TOTAL:
        raise ValueError("Archive total exceeded")
    private_root(destination.parent)
    stage = Path(tempfile.mkdtemp(prefix=".restore-handoff-", dir=destination.parent))
    seen = set()
    try:
        volumes = set()
        for v in m["volumes"]:
            name = safe_name(v["name"])
            if "/" in name or name in volumes:
                raise ValueError("Invalid volume name")
            volumes.add(name)
            path = source / name
            if path.is_symlink() or path.stat().st_size > MAX_FILE:
                raise ValueError("Invalid volume")
            if digest(path.read_bytes()) != v["sha256"]:
                raise ValueError("Volume digest mismatch")
            with zipfile.ZipFile(path) as z:
                for i in z.infolist():
                    name = safe_name(i.filename)
                    if (
                        name not in m["files"]
                        or name in seen
                        or (i.external_attr >> 16) & 0o170000 == 0o120000
                    ):
                        raise ValueError("Duplicate, symlink or unlisted payload")
                    if i.file_size != m["files"][name]["bytes"]:
                        raise ValueError("Payload size mismatch")
                    raw = z.read(i)
                    if digest(raw) != m["files"][name]["sha256"]:
                        raise ValueError("Payload digest mismatch")
                    target = stage / name
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    target.write_bytes(raw)
                    target.chmod(0o600)
                    seen.add(name)
        if seen != set(m["files"]):
            raise ValueError("Missing payloads")
        checks = check(stage)
        stage.rename(destination)
        return {"payloads_verified": len(seen), "checks": checks}
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    q = sub.add_parser("backup")
    q.add_argument("destination", type=Path)
    q.add_argument("--library", type=Path, default=Path("data/jri"))
    q.add_argument("--directory", type=Path, default=Path("data/jri-directory"))
    q.add_argument("--evaluation", type=Path, default=Path("data/jri004-owner-baseline"))
    q.add_argument("--volume-mib", type=int, choices=range(1, 31), default=8)
    q = sub.add_parser("restore")
    q.add_argument("backup_root", type=Path)
    q.add_argument("destination", type=Path)
    q.add_argument("--manifest-sha256", required=True)
    a = p.parse_args()
    result = (
        backup(
            a.destination,
            a.library,
            a.directory,
            a.evaluation,
            volume_bytes=a.volume_mib * 1024 * 1024,
        )
        if a.command == "backup"
        else restore(a.backup_root, a.destination, a.manifest_sha256)
    )
    print(dump(result))


if __name__ == "__main__":
    main()
