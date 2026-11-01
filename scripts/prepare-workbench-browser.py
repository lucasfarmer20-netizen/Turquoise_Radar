"""Create a new disposable /tmp database for synthetic workbench layout checks."""

import argparse
from pathlib import Path

from radar.db import Listing, database
from radar.schemas import ListingInput


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    path = Path(args.database).resolve()
    if not path.is_relative_to(Path("/tmp")) or path.exists():
        raise SystemExit("Use a NEW /tmp database; existing/private data is never replaced")
    engine, sessions = database("sqlite:///" + str(path))
    with sessions.begin() as session:
        for number, source in enumerate(
            ["SYNTHETIC RESALE", "SYNTHETIC AUCTION", "SYNTHETIC LOT"], 1
        ):
            item = ListingInput(
                source=source,
                source_listing_id="SYNTHETIC-" + "identifier" * 12,
                raw_title="SYNTHETIC mobile workbench " + str(number),
                raw_description=(
                    "SYNTHETIC evidence with long URL and unbroken observation token: "
                    "https://example.invalid/"
                    + "reference-" * 45
                    + " "
                    + "UNCONFIRMED_EVIDENCE_" * 16
                ),
                listing_url="https://example.invalid/synthetic-" + str(number),
                shipping_evidence="SYNTHETIC quote reference: " + "shipping_evidence_" * 30,
                resale_basis="SYNTHETIC unverified citation: https://example.invalid/"
                + "citation-" * 60,
                shipping_status="UNKNOWN",
                item_price="30.00" if number == 1 else None,
                shipping_cost="12.00" if number == 1 else None,
                cost_status={"item_price": "ESTIMATED", "shipping_cost": "CONFIRMED"},
                currency="USD" if number != 3 else "CAD",
                pasted_text="SYNTHETIC raw record: " + "original-observation-" * 45,
                synthetic=True,
            )
            session.add(
                Listing(
                    dedup_key=f"synthetic-workbench-layout-{number}",
                    data=item.model_dump(mode="json"),
                    original_data={"synthetic": True, "raw_evidence": "long-token-" * 100},
                    review_state="RESEARCH",
                )
            )
    engine.dispose()
    print("SYNTHETIC workbench database:", path)


if __name__ == "__main__":
    main()
