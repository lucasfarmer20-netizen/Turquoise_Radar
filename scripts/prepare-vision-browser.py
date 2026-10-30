"""Generate only synthetic browser data in a NEW /tmp database. Never owner inventory."""

import argparse
from pathlib import Path

from radar.db import Listing, database
from radar.jewelry.demo import synthetic_photos
from radar.schemas import ListingInput


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="/tmp/turquoise-vision-browser.db")
    args = parser.parse_args()
    path = Path(args.database).resolve()
    if not path.is_relative_to(Path("/tmp")) or path.exists():
        raise SystemExit("Use a new database path below /tmp; no existing data is replaced")
    engine, sessions = database("sqlite:///" + str(path))
    with sessions.begin() as s:
        for n, title in enumerate(
            ["SYNTHETIC old blue stone ring", "SYNTHETIC mixed jewelry lot"], 1
        ):
            item = ListingInput(
                source="SYNTHETIC BROWSER",
                raw_title=title,
                raw_description=(
                    "SYNTHETIC seller description; material, shipping and resale unknown."
                ),
                synthetic=True,
            )
            s.add(
                Listing(
                    dedup_key=f"synthetic-browser-{n}",
                    data=item.model_dump(mode="json"),
                    original_data={"synthetic": True},
                )
            )
    directory = Path("/tmp/turquoise-vision-browser-photos")
    directory.mkdir(mode=0o700, exist_ok=True)
    for n, (image, _) in enumerate(synthetic_photos()):
        (directory / f"{n}.png").write_bytes(image.data)
    (directory / "closeup.png").write_bytes(synthetic_photos(mixed=True)[1][0].data)
    engine.dispose()
    print("SYNTHETIC browser database and generated illustrations prepared:", path)


if __name__ == "__main__":
    main()
