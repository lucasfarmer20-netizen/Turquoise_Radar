// Seed a NEW /tmp DB with prepare-workbench-browser.py and serve it on loopback.
// No writes, source navigation, external images, API calls or owner decisions.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
(async () => {
  const base = process.env.WORKBENCH_BASE || 'http://127.0.0.1:8827';
  assert.equal(new URL(base).hostname, '127.0.0.1');
  const ids = (process.env.WORKBENCH_IDS || '1,2,3').split(',');
  assert(ids.every(id => /^\d+$/.test(id)));
  const browser = await chromium.launch({executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium',
    headless: true, args: ['--no-sandbox']});
  const errors = [], external = [], checks = [];
  try {
    for (const width of [390, 320, 1440]) {
      const context = await browser.newContext({viewport: {width, height: 1000}});
      await context.route('**/*', route => {
        if (new URL(route.request().url()).origin !== base) {
          external.push(route.request().url()); return route.abort();
        }
        assert.equal(route.request().method(), 'GET');
        return route.continue();
      });
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      for (const id of ids) {
        assert.equal((await page.goto(base + '/listings/' + id)).status(), 200);
        // Expanded original evidence must also remain reachable and readable.
        await page.locator('details').evaluateAll(nodes => nodes.forEach(node => node.open = true));
        const body = await page.locator('main').innerText();
        assert(body.includes('Alaska fulfillment: UNKNOWN'));
        assert(body.includes('Currency:'));
        assert(body.includes('maximum safe bid/offer: UNKNOWN'));
        const layout = await page.evaluate(() => ({
          viewport: innerWidth, document: document.documentElement.scrollWidth,
          clipped: getComputedStyle(document.body).overflowX === 'hidden',
          tables: [...document.querySelectorAll('.table-wrap')].map(node => ({
            left: node.getBoundingClientRect().left, right: node.getBoundingClientRect().right,
            overflow: getComputedStyle(node).overflowX,
            scrollable: node.scrollWidth > node.clientWidth
          }))
        }));
        assert(!layout.clipped, 'Do not conceal document overflow');
        assert(layout.document <= width + 2, JSON.stringify({id, ...layout}));
        for (const table of layout.tables) {
          assert(table.left >= 0 && table.right <= width + 2);
          if (table.scrollable) assert(['auto', 'scroll'].includes(table.overflow));
        }
        for (const name of ['Open source listing ↗', 'Edit evidence & recalculate', 'Identify this jewelry']) {
          const link = page.getByRole('link', {name, exact: true});
          await link.scrollIntoViewIfNeeded();
          const bounds = await link.boundingBox();
          assert(bounds && bounds.width > 0 && bounds.x >= 0 && bounds.x + bounds.width <= width + 2);
        }
        // Existing local handoff stays accessible; this GET does not create a case.
        await page.getByRole('link', {name: 'Identify this jewelry', exact: true}).click();
        assert(page.url().includes('/identify'));
        checks.push({id, width, document_width: layout.document, tables: layout.tables});
      }
      await context.close();
    }
    assert.deepEqual(errors, []); assert.deepEqual(external, []);
    const report = {checks, errors, external_requests: external, read_only: true};
    if (process.env.WORKBENCH_REPORT) fs.writeFileSync(process.env.WORKBENCH_REPORT, JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
