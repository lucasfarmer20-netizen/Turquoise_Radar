"""Official API adapters. Search metadata is discovery, never verified training truth."""

import html
import re
from urllib.parse import urlencode

from radar.references.network import StopCollection
from radar.references.schema import Assertion, Item, PhotoRef

CC0 = "https://creativecommons.org/publicdomain/zero/1.0/"


class RejectRecord(Exception):
    """A permanently missing catalog record, not a transport/access-policy failure."""

    reason = "SOURCE_OBJECT_NOT_FOUND"


def text(value):
    if value is None:
        return ""
    return html.unescape(re.sub(r"<[^>]*>", "", str(value))).strip()


def asserted(raw, mapping, page, origin="MUSEUM"):
    return [
        Assertion(field=field, value=text(raw[key])[:12000], origin=origin, evidence_url=page)
        for key, field in mapping.items()
        if raw.get(key) not in (None, "", [], {})
    ]


def image(url, license="CC0", license_url=CC0, evidence="", credit=""):
    return PhotoRef(
        url=url, license=license, license_url=license_url, evidence=evidence, credit=text(credit)
    )


class CMA:
    def page(self, http, query, cursor, size):
        offset = cursor.get("offset", 0)
        data = http.json(
            "https://openaccess-api.clevelandart.org/api/artworks/?"
            + urlencode({"q": query, "cc0": "", "skip": offset, "limit": size})
        )
        rows = data["data"]
        return rows, {"offset": offset + len(rows)}, offset + len(rows) >= data["info"]["total"]

    def item(self, http, raw):
        page = raw.get("url") or f"https://www.clevelandart.org/art/{raw['accession_number']}"
        images = []
        if raw.get("share_license_status") == "CC0":
            url = raw.get("images", {}).get("web", {}).get("url")
            if url:
                images.append(
                    image(
                        url,
                        evidence="share_license_status=CC0",
                        credit=raw.get("creditline", "Cleveland Museum of Art"),
                    )
                )
        artists = [text(x.get("description", "")) for x in raw.get("creators", [])]
        return Item(
            external_id=str(raw["id"]),
            page_url=page,
            title=text(raw["title"]),
            object_type=text(raw.get("type")),
            description=text(raw.get("description")),
            artists=[a for a in artists if a],
            photos=images,
            assertions=asserted(
                raw,
                {
                    "technique": "construction_or_medium",
                    "creation_date": "attributed_date",
                    "measurements": "stated_dimensions",
                    "dimensions": "source_dimensions",
                    "culture": "cultural_attribution",
                    "provenance": "provenance",
                    "inscriptions": "reported_marks",
                    "creators": "maker_attribution",
                },
                page,
            ),
        )


class Met:
    def page(self, http, query, cursor, size):
        offset = cursor.get("offset", 0)
        data = http.json(
            "https://collectionapi.metmuseum.org/public/collection/v1.1/search?"
            + urlencode({"q": query, "hasImages": "true", "offset": offset, "limit": size})
        )
        ids = data.get("objectIDs") or []
        return (
            [{"id": i} for i in ids],
            {"offset": offset + len(ids)},
            offset + len(ids) >= data["total"],
        )

    def item(self, http, raw):
        try:
            r = http.json(
                f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{raw['id']}"
            )
        except StopCollection as exc:
            if exc.category == "HTTP_ERROR" and exc.status == 404:
                raise RejectRecord() from None
            raise
        page = r["objectURL"]
        photos = []
        if r.get("isPublicDomain") is True:
            for url in [r.get("primaryImage"), *(r.get("additionalImages") or [])]:
                if url:
                    photos.append(
                        image(
                            url,
                            evidence="isPublicDomain=true",
                            credit=r.get("creditLine", "The Metropolitan Museum of Art"),
                        )
                    )
        return Item(
            external_id=str(r["objectID"]),
            page_url=page,
            title=text(r["title"]),
            object_type=text(r.get("objectName")),
            artists=[r["artistDisplayName"]] if r.get("artistDisplayName") else [],
            photos=photos,
            assertions=asserted(
                r,
                {
                    "artistDisplayName": "maker_attribution",
                    "artistPrefix": "attribution_qualifier",
                    "artistSuffix": "attribution_scope",
                    "artistDisplayBio": "attribution_context",
                    "objectDate": "attributed_date",
                    "medium": "stated_material",
                    "dimensions": "stated_dimensions",
                    "measurements": "source_measurements",
                    "creditLine": "source_credit",
                    "objectNumber": "catalog_identity",
                    "classification": "source_classification",
                    "culture": "cultural_attribution",
                    "country": "attributed_origin",
                },
                page,
            ),
        )


class AIC:
    fields = (
        "id,title,artist_title,artist_display,date_display,medium_display,dimensions,"
        "description,artwork_type_title,is_public_domain,image_id,alt_image_ids,credit_line"
    )

    def page(self, http, query, cursor, size):
        page = cursor.get("page", 1)
        d = http.json(
            "https://api.artic.edu/api/v1/artworks/search?"
            + urlencode(
                {
                    "q": query,
                    "query[term][is_public_domain]": "true",
                    "limit": size,
                    "page": page,
                    "fields": self.fields,
                }
            )
        )
        return d["data"], {"page": page + 1}, page >= d["pagination"]["total_pages"]

    def item(self, http, r):
        page = f"https://www.artic.edu/artworks/{r['id']}"
        photos = []
        if r.get("is_public_domain") is True:
            for id_ in [r.get("image_id"), *(r.get("alt_image_ids") or [])]:
                if id_ and re.fullmatch(r"[a-zA-Z0-9-]+", id_):
                    photos.append(
                        image(
                            f"https://www.artic.edu/iiif/2/{id_}/full/1686,/0/default.jpg",
                            evidence="is_public_domain=true",
                            credit=r.get("credit_line", "Art Institute of Chicago"),
                        )
                    )
        return Item(
            external_id=str(r["id"]),
            page_url=page,
            title=text(r["title"]),
            object_type=text(r.get("artwork_type_title")),
            description=text(r.get("description")),
            artists=[r["artist_title"]] if r.get("artist_title") else [],
            photos=photos,
            assertions=asserted(
                r,
                {
                    "artist_display": "maker_attribution",
                    "date_display": "attributed_date",
                    "medium_display": "stated_material",
                    "dimensions": "stated_dimensions",
                },
                page,
            ),
        )


class Commons:
    def page(self, http, query, cursor, size):
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": 6,
            "gsrlimit": size,
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "maxlag": 5,
            **cursor,
        }
        d = http.json("https://commons.wikimedia.org/w/api.php?" + urlencode(params))
        return (
            list(d.get("query", {}).get("pages", {}).values()),
            d.get("continue", {}),
            "continue" not in d,
        )

    def item(self, http, r):
        info = (r.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})

        def get(k):
            return text(meta.get(k, {}).get("value", ""))

        license_name, license_url = get("LicenseShortName"), get("LicenseUrl")
        page = info.get("descriptionurl") or "https://commons.wikimedia.org/wiki/" + r[
            "title"
        ].replace(" ", "_")
        photos = []
        allowed = {
            "CC0": CC0,
            "Public domain": "https://creativecommons.org/publicdomain/mark/1.0/",
            "CC BY 4.0": "https://creativecommons.org/licenses/by/4.0/",
        }
        if license_name in allowed and info.get("url"):
            expected = allowed[license_name]
            if license_url.rstrip("/") in (
                expected.rstrip("/"),
                expected.replace("https:", "http:").rstrip("/"),
            ):
                photos = [
                    image(
                        info["url"],
                        license_name,
                        expected,
                        "Commons per-file extmetadata; no warranty of rights",
                        get("Artist"),
                    )
                ]
        description = get("ImageDescription")
        return Item(
            external_id=str(r["pageid"]),
            page_url=page,
            title=r["title"],
            description=description[:30000],
            photos=photos,
            assertions=[
                Assertion(
                    field="source_description",
                    value=description[:12000],
                    origin="COMMUNITY_CATALOG",
                    evidence_url=page,
                )
            ]
            if description
            else [],
        )


ADAPTERS = {"cma": CMA, "met": Met, "aic": AIC, "commons": Commons}


class Manifest:
    """Byte-offset pagination over a hash-bound, manually reviewed local JSONL manifest."""

    def __init__(self, path, source):
        import hashlib
        from pathlib import Path

        self.path, self.source = Path(path), source
        with self.path.open("rb") as f:
            self.digest = hashlib.file_digest(f, "sha256").hexdigest()

    def page(self, http, query, cursor, size):
        import json

        rows = []
        with self.path.open("rb") as f:
            f.seek(cursor.get("offset", 0))
            for _ in range(size):
                line = f.readline(1024 * 1024 + 1)
                if not line:
                    break
                if len(line) > 1024 * 1024:
                    raise ValueError("Manifest line too large")
                raw = json.loads(line)
                rows.append(raw)
            offset = f.tell()
            done = not f.read(1)
        return rows, {"offset": offset}, done

    def item(self, http, raw):
        item = Item.model_validate(raw)
        if self.source.classification == "INTERNAL_FAIR_USE":
            for photo in item.photos:
                photo.license = "UNLICENSED_INTERNAL_FAIR_USE"
                photo.license_url = self.source.terms_url
                photo.evidence = self.source.rationale
        return item
