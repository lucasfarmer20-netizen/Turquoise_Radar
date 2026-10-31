"""Factual mark-directory adapters. Captions are source claims, never authentication."""

import re
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin

from radar.references.recognition import normalize_text


class Cells(HTMLParser):
    """Extract leaf table cells without conflating adjacent makers or nested tables."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.cells, self.links = [], [], []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag == "td":
            self.stack.append({"lines": [""], "images": [], "nested": False})
            if len(self.stack) > 1:
                self.stack[-2]["nested"] = True
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if not self.stack or self.hidden:
            return
        cell = self.stack[-1]
        if tag == "br":
            cell["lines"].append("")
        if tag == "img" and attrs.get("src"):
            cell["images"].append({"url": attrs["src"], "description": attrs.get("alt", "")})

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag == "td" and self.stack:
            cell = self.stack.pop()
            if not cell["nested"]:
                self.cells.append(cell)

    def handle_data(self, data):
        if self.stack and not self.hidden:
            self.stack[-1]["lines"][-1] += data


def art_amerindien(raw, page_url):
    """One factual maker/mark entry per captioned cell, not one per photograph."""
    p = Cells()
    p.feed(raw.decode("utf-8", errors="replace"))
    records = []
    for cell in p.cells:
        images = [i for i in cell["images"] if "native_american_marks/" in i["url"]]
        lines = [" ".join(x.split()) for x in cell["lines"] if x.strip()]
        if not images or not lines:
            continue
        # A site caption may be uncertain or name several people; retain it verbatim.
        caption = " / ".join(lines)
        if len(caption) > 1000:
            continue
        context = re.search(r"\b(Navajo|Hopi|Zuni|Kewa|Santo Domingo|Cherokee)\b", caption, re.I)
        name = re.split(r"\b(?:Navajo|Hopi|Zuni|Kewa|Santo Domingo|Cherokee)\b", lines[0])[0]
        name = re.sub(r"\s+\d+$", "", name).strip(" /,")
        if len(name) < 2 or normalize_text(name) in {"UNKNOWN", "UNIDENTIFIED"}:
            continue
        urls = sorted({urljoin(page_url, i["url"]) for i in images})
        # Same image/caption repeated on another directory page is the same variant.
        locator = "|".join(urls)
        descriptions = list(dict.fromkeys(i["description"].strip() for i in images))
        records.append(
            {
                "locator": locator,
                "maker_name": name,
                "aliases": [],
                "identity_kind": "UNRESOLVED",
                "cultural_context": context.group(0) if context else None,
                "source_caption": caption,
                "transcription": None,
                "transcription_status": "NOT_REVIEWED",
                "description": " / ".join(descriptions)[:2000],
                "image_urls": urls,
                "source_url": page_url,
                "uncertainty": "SOURCE_EXPRESSES_UNCERTAINTY"
                if "?" in caption
                else "SOURCE_ATTRIBUTED",
                "materials": [],
                "period": None,
                "contradictions": [],
            }
        )
    return records


def directory_links(raw, page_url):
    p = Cells()
    p.feed(raw.decode("utf-8", errors="replace"))
    return sorted(
        {
            urldefrag(urljoin(page_url, link))[0]
            for link in p.links
            if "jewelry-marks.htm" in link
            or "signature-hallmarks.htm" in link
            or "shop-trademarks.htm" in link
        }
    )


ADAPTERS = {"art-amerindien": art_amerindien}
