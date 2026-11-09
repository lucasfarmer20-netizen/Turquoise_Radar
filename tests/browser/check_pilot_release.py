"""Browser-only regression; use a freshly rendered synthetic HTML fixture."""

import shutil
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

html = Path(sys.argv[1]).read_text()
js = (Path(__file__).resolve().parents[2] / "radar/static/compact-feed.js").read_text()
with sync_playwright() as p:
    b = p.chromium.launch(
        executable_path=shutil.which("chromium"), headless=True, args=["--no-sandbox"]
    )
    for width in (1280, 390):
        page = b.new_page(viewport={"width": width, "height": 900})
        external = []

        def route(r, external=external):
            if not r.request.url.startswith("http://radar.test/"):
                external.append(r.request.url)
                r.abort()
                return
            if r.request.url.endswith("compact-feed.js"):
                r.fulfill(status=200, content_type="text/javascript", body=js)
            elif r.request.resource_type == "document":
                r.fulfill(
                    status=200,
                    content_type="text/html",
                    body=html,
                    headers={"Cache-Control": "private, no-store"},
                )
            else:
                r.fulfill(status=200, content_type="text/plain", body="")

        page.route("**/*", route)
        page.goto("http://radar.test/opportunities")
        page.wait_for_function(
            "document.querySelector('[data-source-expiry]') && "
            "!document.querySelector('[data-source-expiry]').hidden"
        )
        page.evaluate(
            "window.dispatchEvent(new Event('blur'));window.dispatchEvent(new Event('focus'))"
        )
        assert page.locator("[data-source-expiry]").count() == 0
        assert page.locator("#feed-expiry-status").is_visible()
        assert "Saved decisions are preserved" in page.locator("#feed-expiry-status").inner_text()
        page.reload()
        page.wait_for_function(
            "document.querySelector('[data-source-expiry]') && "
            "!document.querySelector('[data-source-expiry]').hidden"
        )
        # Advance the wall clock past the recorded auction end, but not source expiry.
        page.evaluate(
            "Date.now = () => new Date("
            "document.querySelector('[data-deadline]').dataset.deadline).getTime() + 1000"
        )
        page.wait_for_function(
            "document.querySelector('[data-deadline]').textContent === 'ENDED'", timeout=35000
        )
        assert "Ended" in page.locator("[data-reason]").inner_text()
        page.evaluate(
            "window.dispatchEvent(new Event('pagehide'));window.dispatchEvent(new Event('focus'))"
        )
        assert page.locator("[data-source-expiry]").count() == 0
        assert page.locator("#feed-expiry-status").is_visible()
        assert "Saved decisions are preserved" in page.locator("#feed-expiry-status").inner_text()
        assert "/opportunities" in page.locator("#feed-expiry-status a").get_attribute("href")
        assert not external
        print(
            "PASS",
            width,
            "focus withdrawal/reload, end countdown, resume notice, zero external requests",
        )
        page.close()
    b.close()
