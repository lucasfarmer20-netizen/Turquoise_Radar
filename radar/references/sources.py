"""Seed reviews are idempotent; existing exclusions/reviews are never overwritten."""

from radar.references.schema import Source
from radar.references.store import put_source

REVIEWS = [
    (
        "cma",
        "Cleveland Museum of Art",
        "https://www.clevelandart.org",
        "https://openaccess-api.clevelandart.org/",
        ["openaccess-api.clevelandart.org", "openaccess-cdn.clevelandart.org"],
        "Require share_license_status=CC0 per object, retain attribution and source URL.",
    ),
    (
        "met",
        "Metropolitan Museum of Art",
        "https://www.metmuseum.org",
        "https://www.metmuseum.org/policies/image-resources",
        ["collectionapi.metmuseum.org", "images.metmuseum.org"],
        "Require isPublicDomain=true; official API image URLs only. API limit 80 requests/second; "
        "collector uses at most one per two seconds per host.",
    ),
    (
        "aic",
        "Art Institute of Chicago",
        "https://www.artic.edu",
        "https://api.artic.edu/docs/#copyright",
        ["api.artic.edu", "www.artic.edu"],
        "Require is_public_domain=true; use official IIIF images; respect deep-pagination limits.",
    ),
    (
        "commons",
        "Wikimedia Commons",
        "https://commons.wikimedia.org",
        "https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia",
        ["commons.wikimedia.org", "upload.wikimedia.org"],
        "Evaluate each file. Initial collector accepts CC0/public-domain and CC BY 4.0 only; "
        "Preserve creator, attribution and license URL. "
        "Other licenses remain excluded pending review.",
    ),
]
REFERENCE_ONLY = [
    (
        "art-amerindien",
        "Art-Amerindien",
        "https://www.art-amerindien.com/hallmarks/",
        "https://www.art-amerindien.com/hallmarks/",
        "Copyright notice; collection method not assessed.",
    ),
    (
        "tskies",
        "T.Skies",
        "https://tskies.com/pages/silversmiths",
        "https://tskies.com/policies/terms-of-service",
        "Published personal/noncommercial-use terms.",
    ),
    (
        "medicine-man",
        "Medicine Man Gallery",
        "https://www.medicinemangallery.com/collections/identify-native-american-indian-jewelry-hallmarks",
        "https://www.medicinemangallery.com/policies/terms-of-service",
        "Published restrictions on copying/commercial use; no collection approval.",
    ),
    (
        "perry-null",
        "Perry Null Trading",
        "https://perrynulltrading.com/pages/artists",
        "https://perrynulltrading.com/policies/terms-of-service",
        "Candidate vintage/artist reference; source-specific collection terms not established. "
        "Not eligible for INTERNAL_FAIR_USE until reviewed.",
    ),
]


def seed(c):
    for id_, name, url, terms, hosts, restrictions in REVIEWS:
        if c.execute("SELECT 1 FROM sources WHERE id=?", (id_,)).fetchone():
            continue
        put_source(
            c,
            Source(
                id=id_,
                name=name,
                url=url,
                terms_url=terms,
                reviewed_on="2026-10-30",
                classification="OPEN_ACCESS",
                enabled=True,
                access_method="PUBLIC_API",
                hosts=hosts,
                restrictions=restrictions,
                rationale="Official public API and item-level image rights gate; "
                "no blanket image permission inferred from metadata rights.",
                provenance="Official documentation reviewed for JRI-001; live access unverified.",
                rights_claim="ITEM_LEVEL_LICENSE_REQUIRED",
            ),
        )
    for id_, name, url, terms, restrictions in REFERENCE_ONLY:
        if c.execute("SELECT 1 FROM sources WHERE id=?", (id_,)).fetchone():
            continue
        put_source(
            c,
            Source(
                id=id_,
                name=name,
                url=url,
                terms_url=terms,
                reviewed_on="2026-10-30",
                classification="REFERENCE_ONLY",
                access_method="LINK_ONLY",
                restrictions=restrictions,
                rationale="Research link only, pending separate assessment.",
                provenance="Candidate source from owner brief or hallmark-directory reference.",
            ),
        )
    c.commit()
