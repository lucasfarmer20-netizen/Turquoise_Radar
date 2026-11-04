"""Optional owner-reviewed framing of private ShopGoodwill/HiBid images.

Only presentation coordinates are read. Source pixels, photo records and originals
are never rewritten. eBay framing entries are intentionally outside this feature.
"""

import io
import json
import os
import stat
import warnings
from pathlib import Path
from typing import Annotated, Literal

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

MANIFEST_NAME = "feed-photo-framing.json"
MAX_MANIFEST_BYTES = 128_000
MAX_FRAMES = 100
Offset = Annotated[int, Field(strict=True, ge=0, le=9999)]
Dimension = Annotated[int, Field(strict=True, ge=1, le=10000)]


class PhotoFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    photo_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    source: Literal["ShopGoodwill", "HiBid"]
    source_listing_id: str = Field(pattern=r"^[0-9]{1,20}$")
    original_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_width: Dimension
    source_height: Dimension
    classification: Literal["full_item", "excluded"]
    bbox: tuple[Offset, Offset, Dimension, Dimension] | None = None
    review_note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def bounded_frame(self):
        if self.source_width * self.source_height > 24_000_000:
            raise ValueError("Framing source dimensions exceed the image limit")
        if (self.classification == "full_item") != (self.bbox is not None):
            raise ValueError("Only a full-item review has presentation bounds")
        if self.bbox:
            x, y, width, height = self.bbox
            if x + width > self.source_width or y + height > self.source_height:
                raise ValueError("Presentation bounds exceed the source image")
        return self


class PhotoFraming(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["feed-photo-framing-1"] = Field(alias="schema")
    frames: list[PhotoFrame] = Field(max_length=MAX_FRAMES)

    @model_validator(mode="after")
    def distinct_photos(self):
        if len({entry.photo_id for entry in self.frames}) != len(self.frames):
            raise ValueError("A photo cannot have multiple framing reviews")
        return self


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate framing field")
        result[key] = value
    return result


def load_photo_framing(photo_root):
    """Return reviewed frames, {} when absent, or None on any invalid/unsafe file.

    An invalid existing manifest disables all feed thumbnails, so an invalid
    exclusion cannot silently turn back into an automatic full-frame image.
    """
    root = Path(photo_root).absolute().parent
    directory = None
    try:
        directory = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY)
        for part in root.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        try:
            fd = os.open(
                MANIFEST_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
            )
        except FileNotFoundError:
            return {}
        with os.fdopen(fd, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or not 1 <= metadata.st_size <= MAX_MANIFEST_BYTES
            ):
                return None
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) != metadata.st_size or len(raw) > MAX_MANIFEST_BYTES:
            return None
        manifest = PhotoFraming.model_validate(
            json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        )
        return {entry.photo_id: entry for entry in manifest.frames}
    except (OSError, UnicodeError, ValueError, ValidationError, RecursionError):
        return None
    finally:
        if directory is not None:
            os.close(directory)


def framing_request(manifest, item, photo, *, attached):
    """Reject unreviewed detail/document images before reading their pixels."""
    if manifest is None:
        return None
    entry = manifest.get(photo.id)
    if entry is not None:
        if (
            entry.classification != "full_item"
            or entry.source != item.source
            or entry.source_listing_id != item.source_listing_id
            or entry.original_sha256 != photo.original_hash
            or entry.content_sha256 != photo.content_hash
        ):
            return None
        return entry
    return "direct" if photo.role == "FRONT" and not attached else None


def validated_photo_frame(request, raw, *, allow_original=False):
    """Describe original pixels; source JPEG/WebP permits only a full-image viewport."""
    if request is None:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                width, height = source.size
                if (
                    source.format
                    not in (
                        {"PNG", "JPEG", "WEBP"}
                        if allow_original and request == "direct"
                        else {"PNG"}
                    )
                    or not 16 <= min(width, height)
                    or max(width, height) > 10000
                    or width * height > 24_000_000
                    or getattr(source, "n_frames", 1) != 1
                ):
                    return None
                source.verify()
    except (
        OSError,
        ValueError,
        SyntaxError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        return None
    if request == "direct":
        x, y, frame_width, frame_height = 0, 0, width, height
        kind, note = "direct", None
    else:
        if (request.source_width, request.source_height) != (width, height):
            return None
        x, y, frame_width, frame_height = request.bbox
        kind, note = "full_item", request.review_note
    return dict(
        kind=kind,
        source_width=width,
        source_height=height,
        x=x,
        y=y,
        width=frame_width,
        height=frame_height,
        max_width=frame_width,
        max_height=frame_height,
        review_note=note,
    )
