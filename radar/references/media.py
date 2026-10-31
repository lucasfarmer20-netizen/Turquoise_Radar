"""Validated private originals and normalized derivatives; never alter source bytes."""

import hashlib
import io
import os
import tempfile
import warnings
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 25_000_000


class InvalidImage(ValueError):
    pass


def normalize(raw):
    if not raw or len(raw) > MAX_BYTES:
        raise InvalidImage("IMAGE_SIZE")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as probe:
                format_ = probe.format
                if format_ not in {"PNG", "JPEG", "WEBP"}:
                    raise InvalidImage("IMAGE_FORMAT")
                width, height = probe.size
                if min(width, height) < 64 or width * height > MAX_PIXELS:
                    raise InvalidImage("IMAGE_DIMENSIONS")
                if getattr(probe, "n_frames", 1) != 1:
                    raise InvalidImage("IMAGE_ANIMATED")
                probe.verify()
            with Image.open(io.BytesIO(raw)) as im:
                im.load()
                rgb = ImageOps.exif_transpose(im).convert("RGBA")
                bg = Image.new("RGBA", rgb.size, (255, 255, 255, 255))
                bg.alpha_composite(rgb)
                im = bg.convert("RGB")
                im.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                # New pixel-only image excludes EXIF, PNG text and inherited info.
                clean = Image.frombytes("RGB", im.size, im.tobytes())
                small = list(clean.convert("L").resize((9, 8)).get_flattened_data())
                dhash = 0
                for y in range(8):
                    for x in range(8):
                        dhash = (dhash << 1) | (small[y * 9 + x] > small[y * 9 + x + 1])
                buf = io.BytesIO()
                clean.save(buf, format="PNG")
                normalized = buf.getvalue()
                if len(normalized) > MAX_BYTES:
                    raise InvalidImage("IMAGE_SIZE")
                return normalized, {
                    "format": format_,
                    "width": width,
                    "height": height,
                    "normalized_width": clean.width,
                    "normalized_height": clean.height,
                    "original_hash": hashlib.sha256(raw).hexdigest(),
                    "normalized_hash": hashlib.sha256(normalized).hexdigest(),
                    "dhash": f"{dhash:016x}",
                    "processing_version": "jri-pixels-1",
                }
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise InvalidImage("IMAGE_CORRUPT") from None


def write_blob(root, folder, digest, data):
    path = Path(root) / folder / (digest + ".bin" if folder == "originals" else digest + ".png")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise InvalidImage("STORAGE_INTEGRITY")
        return str(path.relative_to(root))
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return str(path.relative_to(root))
