import hashlib
import io
import os
import re
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_FILE_BYTES = 10_000_000
MAX_BATCH_BYTES = 40_000_000
MAX_PIXELS = 24_000_000
MAX_DIMENSION = 10000
PROCESSING_SIDE = 2560


@dataclass(frozen=True)
class CleanImage:
    data: bytes
    original_hash: str
    content_hash: str
    original_format: str
    original_width: int
    original_height: int
    width: int
    height: int


def clean_image(raw):
    if not raw or len(raw) > MAX_FILE_BYTES:
        raise ValueError("Photograph must be 1–10 MB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as check:
                format_ = check.format
                width, height = check.size
                if format_ not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError("Only JPEG, PNG and WebP are supported")
                if (
                    width * height > MAX_PIXELS
                    or max(width, height) > MAX_DIMENSION
                    or min(width, height) < 16
                ):
                    raise ValueError("Unsupported image dimensions (16px minimum, 24MP maximum)")
                if getattr(check, "n_frames", 1) != 1:
                    raise ValueError("Animated images are not supported")
                check.verify()
            with Image.open(io.BytesIO(raw)) as original:
                oriented = ImageOps.exif_transpose(original)
                oriented.thumbnail((PROCESSING_SIDE, PROCESSING_SIDE), Image.Resampling.LANCZOS)
                mode = "RGBA" if "A" in oriented.getbands() else "RGB"
                converted = oriented.convert(mode)
                # New pixels, no EXIF/GPS/ICC/text chunks or trailing/polyglot content.
                clean = Image.frombytes(mode, converted.size, converted.tobytes())
                output = io.BytesIO()
                clean.save(output, format="PNG")
                data = output.getvalue()
                if len(data) > MAX_FILE_BYTES:
                    raise ValueError("Processing copy exceeds storage limit")
                return CleanImage(
                    data,
                    hashlib.sha256(raw).hexdigest(),
                    hashlib.sha256(data).hexdigest(),
                    format_,
                    width,
                    height,
                    clean.width,
                    clean.height,
                )
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as e:
        raise ValueError("Invalid, corrupt or excessive image") from e


def private_path(root, photo_id):
    if not re.fullmatch(r"[a-f0-9]{32}", photo_id):
        raise ValueError("Invalid photo identifier")
    path = root / (photo_id + ".png")
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Unsafe private photo path")
    return path


def write_photo(root, photo_id, data):
    path = private_path(root, photo_id)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as file:
        file.write(data)


def read_photo(root, photo):
    path = private_path(root, photo.id)
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Photo unavailable")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != photo.content_hash:
        raise ValueError("Photo integrity check failed")
    return data
