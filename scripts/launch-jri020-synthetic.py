"""Launch the existing feed with one invented photo in an exclusively new directory.

Requires already installed locked project dependencies. Never installs software.
No live mode exists. Stop with Ctrl-C; remove only this synthetic workspace after review.
"""

import argparse
import io
import json
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def prepare(workspace):
    workspace = Path(workspace).absolute()
    if "new-only-research" in workspace.parts or any(
        p.is_symlink() for p in (workspace, *workspace.parents)
    ):
        raise ValueError("Use a separate new pilot directory")
    workspace.mkdir(mode=0o700, parents=False, exist_ok=False)
    os.environ.update(
        RADAR_DATABASE_URL="sqlite:///" + str(workspace / "pilot.db"),
        VISION_IMAGE_DIR=str(workspace / "photos"),
        EBAY_MODE="fixture",
        EBAY_SOURCE_ENABLED="false",
        EBAY_PRODUCTION_ENABLED="false",
        EBAY_PRODUCTION_APPROVED="false",
        VISION_PROVIDER="fixture",
        VISION_LIVE_ENABLED="false",
    )
    from PIL import Image

    from radar.db import Listing, database
    from radar.jewelry.images import clean_image
    from radar.jewelry.service import add_photos, open_case
    from radar.schemas import ListingInput

    photos = workspace / "photos"
    photos.mkdir(mode=0o700)
    engine, sessions = database(os.environ["RADAR_DATABASE_URL"])
    try:
        with sessions.begin() as session:
            row = Listing(
                dedup_key="SYNTHETIC-JRI020-PHOTO",
                review_state="RESEARCH",
                original_data={"synthetic": True},
                data=ListingInput(
                    source="SYNTHETIC local canary",
                    synthetic=True,
                    raw_title="SYNTHETIC invented photo — no marketplace data",
                ).model_dump(mode="json"),
            )
            session.add(row)
            session.flush()
            listing_id = row.id
        case_id = open_case(sessions, "listing", listing_id)
        raw = io.BytesIO()
        Image.new("RGB", (240, 180), "turquoise").save(raw, format="PNG")
        add_photos(sessions, photos, case_id, [(clean_image(raw.getvalue()), "FRONT")])
    finally:
        engine.dispose()
    (workspace / "pilot.db").chmod(0o600)
    marker = workspace / "SYNTHETIC-ONLY.json"
    marker.write_text(json.dumps({"synthetic_only": True, "live_authorized": False}))
    marker.chmod(0o600)
    return workspace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    # Never interfere with the owner's port 8000, or steal an existing 8001 listener.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 8001))
    prepare(args.workspace)
    if not args.prepare_only:
        import uvicorn

        from radar.main import app

        print("Synthetic feed: http://127.0.0.1:8001/feed?dataset=fixture")
        uvicorn.run(app, host="127.0.0.1", port=8001, access_log=False)


if __name__ == "__main__":
    main()
