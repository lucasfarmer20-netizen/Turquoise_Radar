"""Read-only private downloads for listing-bound, verified auction terms."""

from fastapi import HTTPException
from fastapi.responses import Response

from radar.db import Listing
from radar.research_assets import read_research_asset, research_evidence_root
from radar.schemas import ListingInput

PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
}


def research_routes(app):
    @app.get("/listings/{listing_id}/research-attachments/{artifact_id}")
    def terms_file(listing_id: int, artifact_id: str):
        with app.state.sessions() as session:
            row = session.get(Listing, listing_id)
            if row is None:
                raise HTTPException(404, "Research attachment unavailable", headers=PRIVATE_HEADERS)
            try:
                item = ListingInput.model_validate(row.data)
            except ValueError:
                raise HTTPException(
                    404, "Research attachment unavailable", headers=PRIVATE_HEADERS
                ) from None
            attachment = next(
                (entry for entry in item.research_attachments if entry.artifact_id == artifact_id),
                None,
            )
            if attachment is None or attachment.kind != "AUCTION_TERMS":
                raise HTTPException(404, "Research attachment unavailable", headers=PRIVATE_HEADERS)
            try:
                raw = read_research_asset(research_evidence_root(), attachment)
            except (OSError, ValueError):
                raise HTTPException(
                    404, "Research attachment unavailable", headers=PRIVATE_HEADERS
                ) from None
        return Response(
            raw,
            media_type="text/plain",
            headers={
                **PRIVATE_HEADERS,
                "Content-Disposition": f'attachment; filename="research-terms-{artifact_id}.txt"',
            },
        )
