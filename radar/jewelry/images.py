import hashlib
import io
import os
import re
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_FILE_BYTES = 10_000_000
MAX_BATCH_BYTES = 40_000_000
MAX_PIXELS = 24_000_000
MAX_DIMENSION = 10000
PROCESSING_SIDE = 2560
ORIGINAL_PROCESSING_VERSION = "ebay-primary-original-1"
ORIGINAL_FORMATS = {
    "JPEG": (".jpg", "image/jpeg"),
    "PNG": (".png", "image/png"),
    "WEBP": (".webp", "image/webp"),
}


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


@dataclass(frozen=True)
class OriginalImage(CleanImage):
    media_type: str


def validate_original_image(raw, content_type):
    """Decode and validate official source pixels without rewriting any source bytes."""
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_FILE_BYTES:
        raise ValueError("Source image exceeds the private file limit")
    if not isinstance(content_type, str):
        raise ValueError("Source image Content-Type is required")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type not in {value[1] for value in ORIGINAL_FORMATS.values()}:
        raise ValueError("Unsupported source image Content-Type")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                format_ = image.format
                width, height = image.size
                if format_ not in ORIGINAL_FORMATS or ORIGINAL_FORMATS[format_][1] != media_type:
                    raise ValueError("Source image type and bytes differ")
                if (
                    min(width, height) < 16
                    or max(width, height) > MAX_DIMENSION
                    or width * height > MAX_PIXELS
                    or getattr(image, "n_frames", 1) != 1
                ):
                    raise ValueError("Unsupported source image dimensions or animation")
                image.verify()
            # verify() alone does not decode JPEG pixel data; a truncated stream
            # must never become a stored/displayed source image.
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
        # Reject trailing material rather than stripping it from preserved bytes.
        if format_ == "JPEG" and not raw.endswith(b"\xff\xd9"):
            raise ValueError("Source JPEG has trailing data")
        if format_ == "PNG" and not raw.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82"):
            raise ValueError("Source PNG has trailing data")
        if format_ == "WEBP" and int.from_bytes(raw[4:8], "little") + 8 != len(raw):
            raise ValueError("Source WebP length differs")
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ):
        raise ValueError("Invalid, corrupt or excessive source image") from None
    digest = hashlib.sha256(raw).hexdigest()
    return OriginalImage(raw, digest, digest, format_, width, height, width, height, media_type)


def original_photo_metadata(image):
    """A downloader supplies provenance/timestamps separately before publishing the row."""
    if not isinstance(image, OriginalImage):
        raise ValueError("Validated source image required")
    suffix, media_type = ORIGINAL_FORMATS[image.original_format]
    return dict(
        processing_version=ORIGINAL_PROCESSING_VERSION,
        format=image.original_format,
        original_format=image.original_format,
        storage_suffix=suffix,
        media_type=media_type,
        original_width=image.original_width,
        original_height=image.original_height,
        width=image.width,
        height=image.height,
        bytes=len(image.data),
        metadata_removed=False,
    )


def photo_storage_metadata(data, original_hash, content_hash):
    """Resolve only a strict original-byte contract; legacy sanitized PNGs stay unchanged."""
    if not isinstance(data, dict):
        raise ValueError("Invalid private photo metadata")
    version = data.get("processing_version")
    if version in (None, "1"):
        if (
            data.get("storage_suffix", ".png") != ".png"
            or data.get("media_type", "image/png") != "image/png"
        ):
            raise ValueError("Invalid sanitized photo storage")
        return ".png", "image/png"
    if version != ORIGINAL_PROCESSING_VERSION:
        raise ValueError("Unsupported private photo processing version")
    format_ = data.get("format")
    storage = ORIGINAL_FORMATS.get(format_) if isinstance(format_, str) else None
    width, height, size = data.get("width"), data.get("height"), data.get("bytes")
    if (
        not storage
        or (data.get("storage_suffix"), data.get("media_type")) != storage
        or data.get("format") != data.get("original_format")
        or data.get("metadata_removed") is not False
        or not isinstance(original_hash, str)
        or not re.fullmatch(r"[a-f0-9]{64}", original_hash)
        or original_hash != content_hash
        or any(
            type(value) is not int
            for value in (
                width,
                height,
                size,
                data.get("original_width"),
                data.get("original_height"),
            )
        )
        or not 1 <= size <= MAX_FILE_BYTES
        or not 16 <= min(width, height)
        or max(width, height) > MAX_DIMENSION
        or width * height > MAX_PIXELS
        or (width, height) != (data.get("original_width"), data.get("original_height"))
    ):
        raise ValueError("Invalid exact source photo metadata")
    return storage


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


def private_path(root, photo_id, suffix=".png"):
    root = Path(root)
    if not isinstance(photo_id, str) or not re.fullmatch(r"[a-f0-9]{32}", photo_id):
        raise ValueError("Invalid photo identifier")
    if suffix not in {".png", ".jpg", ".webp"}:
        raise ValueError("Invalid private photo extension")
    path = root / (photo_id + suffix)
    if any(
        parent.is_symlink() for parent in (path, *path.parents)
    ) or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Unsafe private photo path")
    return path


def write_photo(root, photo_id, data, suffix=".png"):
    path = private_path(root, photo_id, suffix)
    if not isinstance(data, bytes) or not 1 <= len(data) <= MAX_FILE_BYTES:
        raise ValueError("Invalid private photo bytes")
    directory, created = None, False
    try:
        root = path.absolute().parent
        directory = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY)
        for part in root.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(
            path.name,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        created = True
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
    except BaseException:
        if created and directory is not None:
            os.unlink(path.name, dir_fd=directory)
        raise
    finally:
        if directory is not None:
            os.close(directory)


def read_photo(root, photo):
    # MakerPhoto predates VisionPhoto and has no original_hash column. Its
    # sanitized PNG contract does not require one; exact source images still do.
    suffix, _ = photo_storage_metadata(
        photo.data, getattr(photo, "original_hash", None), photo.content_hash
    )
    if photo.data.get("processing_version") == ORIGINAL_PROCESSING_VERSION:
        from radar.research_assets import read_research_photo

        return read_research_photo(root, photo)
    path = private_path(root, photo.id, suffix)
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Photo unavailable")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != photo.content_hash:
        raise ValueError("Photo integrity check failed")
    return data
