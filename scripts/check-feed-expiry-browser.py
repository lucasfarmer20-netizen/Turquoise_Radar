"""Offline Chromium qualification. Requires optional Playwright; uses system Chromium.

Run with the repository venv: python scripts/check-feed-expiry-browser.py
Every browser request is fulfilled through TestClient or blocked. No live data.
"""

import hashlib
import json
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from fastapi.testclient import TestClient  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from pytest import MonkeyPatch  # noqa: E402
from test_original_source_images import source_photo  # noqa: E402

from radar.db import DiscoveredItem, VisionPhoto  # noqa: E402
from radar.main import create_app  # noqa: E402


def fingerprint(directory):
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in directory.rglob('*') if path.is_file()
    }


def check(browser, scenario, width):
    with tempfile.TemporaryDirectory(prefix='synthetic-feed-expiry-') as temp:
        directory = Path(temp)
        with MonkeyPatch.context() as mp, TestClient(
            create_app('sqlite:///' + temp + '/synthetic.db')
        ) as client:
            item_id, _, photo_id, _, _ = source_photo(
                client, directory, mp, observed_hours=5.99
            )
            with client.app.state.sessions.begin() as session:
                photo = session.get(VisionPhoto, photo_id)
                if scenario in ('row', 'fixture', 'non-ebay'):
                    photo.active = False
                if scenario == 'image':
                    photo.data = photo.data | {
                        'fetched_at': (datetime.now(UTC) - timedelta(hours=23.995)).isoformat()
                    }
                if scenario in ('fixture', 'non-ebay'):
                    item = session.get(DiscoveredItem, item_id)
                    item.normalized = item.normalized | (
                        {'discovery_mode': 'fixture', 'synthetic': True}
                        if scenario == 'fixture' else {'source': 'ShopGoodwill'}
                    )
            response = client.get('/feed?dataset=all&scope=all')
            assert len(response.context['cards']) == 1
            assert response.headers['cache-control'] == 'private, no-store'
            html = response.text
            compact = response.context['cards'][0]['compact']
            if scenario in ('missing', 'invalid'):
                expiry = compact['source_expires_at']
                replacement = '' if scenario == 'missing' else 'invalid'
                html = html.replace(f'data-source-expires-at="{expiry}"',
                                    f'data-source-expires-at="{replacement}"')
            baseline = fingerprint(directory)
            page = browser.new_page(viewport={'width': width, 'height': 900})
            failures, external, mutations, errors = [], [], [], []
            page.on('pageerror', lambda error: errors.append(str(error)))

            def serve(route):
                request = route.request
                target = urlsplit(request.url)
                if target.netloc != 'synthetic.local':
                    external.append(request.url)
                    route.abort()
                    return
                if request.method != 'GET':
                    mutations.append(request.method)
                    route.abort()
                    return
                if target.path == '/feed':
                    route.fulfill(status=200, body=html, content_type='text/html')
                    return
                if scenario == 'script-blocked' and target.path == '/static/compact-feed.js':
                    route.fulfill(status=200, body='', content_type='application/javascript')
                    return
                result = client.get(target.path)
                if result.status_code != 200:
                    failures.append((target.path, result.status_code))
                route.fulfill(status=result.status_code, body=result.content,
                              headers=dict(result.headers))

            page.route('**/*', serve)
            if scenario != 'native':
                initial_time = datetime.now(UTC)
                if scenario in ('slow-behind', 'cached-behind'):
                    initial_time -= timedelta(days=1)
                page.clock.install(time=initial_time)
                # Playwright's controlled performance implementation omits navigation
                # entries. Supply explicit normal-delivery metadata for these tests.
                page.add_init_script(('''
                    const clockEntries = performance.getEntriesByType.bind(performance);
                    performance.getEntriesByType = (type) => type === 'navigation'
                      ? [{startTime:0, type:'navigate', transferSize:300, deliveryType:''}]
                      : clockEntries(type);
                ''').replace('transferSize:300',
                             'transferSize:0' if scenario.startswith('cached')
                             else 'transferSize:300'))
                if scenario in ('slow-behind', 'slow-delivery'):
                    page.add_init_script('''
                        const actualNow = performance.now.bind(performance);
                        performance.now = () => actualNow() + 120000;
                    ''')
            page.goto('http://synthetic.local/feed')
            if scenario != 'native':
                page.clock.pause_at(initial_time + timedelta(seconds=1))
            marked = page.locator('[data-source-expiry]')
            if scenario == 'script-blocked':
                assert marked.count() == 1
                assert not marked.is_visible()
                page.clock.fast_forward(120000)
                assert not marked.is_visible()
            elif scenario == 'native':
                assert marked.count() == 1
                assert marked.is_visible()
                assert page.locator('.compact-photo image').count() == 1
            elif scenario in ('fixture', 'non-ebay'):
                assert marked.count() == 0
                page.clock.fast_forward(25 * 3600000)
                assert page.locator('[data-feed-key]').count() == 1
            elif scenario in (
                'missing', 'invalid', 'slow-behind', 'slow-delivery', 'cached-behind', 'cached'
            ):
                assert marked.count() == 0
                assert page.locator('#feed-expiry-status').is_visible()
            else:
                assert marked.count() == 1, scenario
                assert marked.is_visible()
                if scenario != 'row':
                    assert page.locator('.compact-photo image').count() == 1
                expiry_ms = datetime.fromisoformat(compact['source_expires_at']).timestamp() * 1000
                if scenario == 'backwards-clock':
                    page.clock.set_system_time(datetime.now(UTC) - timedelta(days=1))
                    page.clock.fast_forward(120000)
                elif scenario in ('frozen-restore', 'frozen-visibility', 'frozen-focus'):
                    # Both clocks remain frozen, so expiry cannot be established locally.
                    if scenario == 'frozen-restore':
                        page.evaluate(
                            "dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))"
                        )
                    elif scenario == 'frozen-focus':
                        page.evaluate("dispatchEvent(new Event('blur'))")
                        page.evaluate("dispatchEvent(new Event('focus'))")
                    else:
                        page.evaluate('''
                            Object.defineProperty(document, 'hidden',
                              {configurable:true, value:true});
                            document.dispatchEvent(new Event('visibilitychange'));
                            Object.defineProperty(document, 'hidden',
                              {configurable:true, value:false});
                            document.dispatchEvent(new Event('visibilitychange'));
                        ''')
                elif scenario in (
                    'pageshow', 'visibility', 'focus', 'interaction', 'auxclick', 'contextmenu'
                ):
                    # Simulate suspended timers with an advanced wall clock.
                    page.clock.set_system_time(datetime.fromtimestamp((expiry_ms + 1) / 1000, UTC))
                    assert marked.count() == 1
                    if scenario == 'pageshow':
                        page.evaluate(
                            "dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))"
                        )
                    elif scenario == 'focus':
                        page.evaluate("dispatchEvent(new Event('focus'))")
                    elif scenario == 'visibility':
                        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                    elif scenario in ('auxclick', 'contextmenu'):
                        allowed = page.locator('.compact-card-links a').first.evaluate(
                            '''(link, type) => link.dispatchEvent(new MouseEvent(type, {
                                bubbles:true, cancelable:true, button:type === 'auxclick' ? 1 : 2
                            }))''', scenario
                        )
                        assert allowed is False
                    else:
                        page.locator('[data-compact-review] button[value=GO]').evaluate(
                            '(button) => button.click()'
                        )
                else:
                    # Match the existing inclusive server boundary, then withdraw at +1ms.
                    remaining = expiry_ms - page.evaluate('Date.now()')
                    page.clock.run_for(int(remaining))
                    assert marked.count() == 1
                    page.clock.run_for(2)
                assert marked.count() == 0
                assert page.locator('.compact-photo image').count() == 0
                assert page.locator('#feed-expiry-status').is_visible()
                assert '1 saved eBay item' in page.locator('#feed-expiry-status').inner_text()
                page.clock.fast_forward(60000)
                assert '1 saved eBay item' in page.locator('#feed-expiry-status').inner_text()
                assert not page.locator('.feed-pagination > span').is_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not external and not mutations and not errors and not failures
            assert fingerprint(directory) == baseline
            page.close()
            return {'scenario': scenario, 'width': width, 'passed': True,
                    'external_requests': 0, 'mutations': 0, 'stored_bytes_unchanged': True}


if __name__ == '__main__':
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path='/usr/bin/chromium', args=['--no-sandbox']
        )
        results = [check(browser, scenario, width) for width in (1440, 390) for scenario in (
            'listing', 'row', 'image', 'pageshow', 'visibility', 'focus', 'interaction',
            'script-blocked', 'native', 'backwards-clock', 'slow-behind', 'slow-delivery',
            'cached-behind', 'cached',
            'frozen-restore', 'frozen-visibility', 'frozen-focus', 'auxclick', 'contextmenu',
            'missing', 'invalid', 'fixture', 'non-ebay',
        )]
        browser.close()
        print(json.dumps({'synthetic_only': True, 'checks': results}, indent=2))
