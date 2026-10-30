"""Explicit synthetic visual workflow, isolated from the default owner database."""

import argparse
import asyncio
import io

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select

from radar.anomalies import seed_patterns
from radar.db import Listing, database
from radar.discovery.service import seed_discovery
from radar.intake.registry import seed_sources
from radar.jewelry.config import VisionConfig, image_root
from radar.jewelry.images import clean_image
from radar.jewelry.service import add_photos, analyze, open_case
from radar.schemas import ListingInput


def synthetic_photos(mixed=False):
    photos = []
    font = ImageFont.load_default(size=24)
    for role in ["FRONT", "BACK_HALLMARK", "STONE", "SIDE_DETAIL"]:
        image = Image.new("RGB", (1000, 700), "#17232c")
        draw = ImageDraw.Draw(image)
        draw.text((32, 25), "SYNTHETIC DEMO / " + role, font=font, fill="#71e0d0")
        draw.ellipse((260, 150, 740, 590), outline="#bfcbd0", width=35)
        draw.ellipse((360, 180, 640, 420), fill="#43b8ac", outline="#e4d4a3", width=12)
        if role == "BACK_HALLMARK":
            draw.rectangle((310, 270, 710, 430), fill="#a7b2b6")
            draw.text((430, 320), "9 ?", font=ImageFont.load_default(size=52), fill="#39464d")
        if mixed:
            draw.ellipse((60, 350, 230, 560), outline="#d0b892", width=22)
            draw.rectangle((750, 250, 910, 500), outline="#c6ccd0", width=16)
        draw.text(
            (32, 640),
            "Illustration only. Not a real listing or material/authenticity evidence.",
            font=ImageFont.load_default(size=20),
            fill="#e4d4a3",
        )
        output = io.BytesIO()
        image.save(output, "PNG")
        photos.append((clean_image(output.getvalue()), role))
    return photos


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="sqlite:///./data/vision-demo.db")
    args = parser.parse_args()
    engine, sessions = database(args.database)
    root = image_root(engine)
    with sessions.begin() as s:
        seed_patterns(s)
        seed_discovery(s)
        seed_sources(s)
        for key, title in [
            ("ring", "SYNTHETIC old blue stone ring"),
            ("lot", "SYNTHETIC mixed estate jewelry lot"),
        ]:
            row = s.scalar(select(Listing).where(Listing.dedup_key == "vision-demo-" + key))
            if not row:
                item = ListingInput(
                    source="SYNTHETIC VISION DEMO",
                    raw_title=title,
                    raw_description=(
                        "SYNTHETIC illustrated research example; "
                        "no resale or material verification."
                    ),
                    synthetic=True,
                )
                s.add(
                    Listing(
                        dedup_key="vision-demo-" + key,
                        data=item.model_dump(mode="json"),
                        original_data={"synthetic": True},
                    )
                )
    with sessions() as s:
        listings = list(
            s.scalars(
                select(Listing).where(
                    Listing.dedup_key.in_(["vision-demo-ring", "vision-demo-lot"])
                )
            )
        )
    for listing in listings:
        case = open_case(sessions, "listing", listing.id)
        add_photos(sessions, root, case, synthetic_photos("lot" in listing.dedup_key))
        from radar.db import VisionPhoto

        with sessions() as s:
            ids = list(
                s.scalars(
                    select(VisionPhoto.id)
                    .where(VisionPhoto.case_id == case, VisionPhoto.active)
                    .order_by(VisionPhoto.created_at, VisionPhoto.id)
                )
            )
        stages = ["QUICK", "DEEP"] if "ring" in listing.dedup_key else ["QUICK"]
        for stage in stages:
            run, cached = asyncio.run(
                analyze(
                    sessions,
                    root,
                    case,
                    ids,
                    VisionConfig(),
                    stage=stage,
                    initiate=True,
                    deep_confirm=stage == "DEEP",
                )
            )
            print(
                f"SYNTHETIC fixture case {case}, {stage}, run {run}, "
                f"cache reused {cached}; no API call"
            )
    engine.dispose()


if __name__ == "__main__":
    main()
