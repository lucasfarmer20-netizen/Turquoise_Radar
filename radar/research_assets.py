"""Read bounded private originals without interpreting or publishing their content."""

import hashlib
import os
import re
import stat
from pathlib import Path

from radar.schemas import ResearchAttachment


def research_evidence_root():
    """Read configuration only; operators create and populate the private directory."""
    return Path(os.getenv("JRI_RESEARCH_EVIDENCE_DIR", "data/private/research-evidence")).absolute()


def _private_bytes(root, basename, size, digest, limit):
    root = Path(root).absolute()
    static = Path(__file__).resolve().parent / "static"
    if root.resolve().is_relative_to(static.resolve()) or any(
        part.is_symlink() for part in (root, *root.parents)
    ):
        raise ValueError("Unsafe private research storage")
    if not re.fullmatch(r"[a-f0-9]{32}\.(png|txt)", basename):
        raise ValueError("Invalid research artifact identifier")
    directory = None
    try:
        # Traverse from an anchored descriptor so even ancestor replacement cannot
        # redirect a read through a symlink between a path check and opening it.
        directory = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY)
        for part in root.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(basename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as file:
            metadata = os.fstat(file.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size != size
                or not 1 <= metadata.st_size <= limit
            ):
                raise ValueError("Private research file size or type does not match")
            data = file.read(limit + 1)
    except OSError:
        raise ValueError("Private research file unavailable") from None
    finally:
        if directory is not None:
            os.close(directory)
    if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Private research file integrity check failed")
    return data


def read_research_photo(root, photo):
    """Validate an existing sanitized photo with the same strict descriptor checks."""
    return _private_bytes(
        root, photo.id + ".png", photo.data.get("bytes"), photo.content_hash, 10_000_000
    )


def read_research_asset(root, attachment):
    """Return exact integrity-checked bytes; never follow a symlink or render markup."""
    attachment = ResearchAttachment.model_validate(attachment)
    suffix = ".png" if attachment.kind == "SCREENSHOT" else ".txt"
    data = _private_bytes(
        root,
        attachment.artifact_id + suffix,
        attachment.bytes,
        attachment.original_sha256,
        10_000_000 if attachment.kind == "SCREENSHOT" else 1_000_000,
    )
    if attachment.kind == "SCREENSHOT":
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Research screenshots must contain original PNG bytes")
    else:
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise ValueError("Research terms must contain UTF-8 text") from None
        if "\x00" in text:
            raise ValueError("Research terms cannot contain binary NUL data")
    return data
