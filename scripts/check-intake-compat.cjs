// Run only against an empty disposable database; see docs/ALERT-SETUP.md.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');

(async () => {
  const base = 'http://127.0.0.1:8004';
  const fixtures = path.resolve(__dirname, '../fixtures/alerts');
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium',
    headless: true,
    args: ['--no-sandbox'],
  });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    const errors = [];
    const external = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => {
      if (message.type() === 'error') errors.push(message.text());
    });
    // Abort even an accidental provider/tracking request before it can be sent.
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin !== base) {
        external.push(route.request().url());
        return route.abort();
      }
      return route.continue();
    });
    await page.goto(base + '/inbox');
    assert.equal(await page.locator('.inbox-card').count(), 0, 'Use an empty disposable DB');
    async function upload(source, files) {
      await page.goto(base + '/inbox/import');
      const form = page.locator('form[action="/inbox/import"]');
      await form.locator('[name="source_id"]').selectOption(source);
      await form.locator('[name="files"]').setInputFiles(files.map(file => path.join(fixtures, file)));
      await form.getByRole('button', { name: 'Import private alerts' }).click();
      await page.getByRole('heading', { name: 'Private intake complete.' }).waitFor();
    }
    await upload('shopgoodwill', ['synthetic-shop-style-bid.eml']);
    await page.goto(base + '/inbox');
    assert.equal(await page.locator('.inbox-card').count(), 1, 'Standalone bid ID must correlate');
    await upload('shopgoodwill', ['synthetic-shop-style-winning.eml']);
    await page.goto(base + '/inbox');
    assert.equal(await page.locator('.inbox-card').count(), 1);
    await page.goto(base + '/invoices');
    assert.equal(await page.getByRole('link', { name: 'SYNTHETIC-COMPAT-1', exact: true }).count(), 0);
    await upload('shopgoodwill', ['synthetic-shop-style-receipt.eml', 'synthetic-shop-style-receipt.eml']);
    await page.goto(base + '/invoices/1');
    assert.match(await page.locator('main').innerText(), /RECONCILED/);
    assert.match(await page.locator('main').innerText(), /27\.00/);
    for (const [key, value] of Object.entries({
      item_price: '18', buyer_premium: '0', buyer_service_fee: '0',
      sales_tax: '0', shipping_cost: '8', handling_fee: '1',
    })) await page.locator(`[name="${key}"]`).fill(value);
    await page.locator('[name="confirm_owned"]').check();
    await page.getByRole('button', { name: 'Confirm paid acquisition' }).click();
    await page.waitForURL(/\/listings\/1$/);
    assert.match(await page.locator('main').innerText(), /PURCHASED/);
    await upload('shopgoodwill', ['synthetic-shop-style-bid.eml', 'synthetic-shop-style-multi-receipt.eml']);
    await page.goto(base + '/listings/1');
    assert.match(await page.locator('main').innerText(), /\$27\.00/);
    await page.goto(base + '/invoices/2');
    assert.equal(await page.getByRole('button', { name: 'Confirm paid acquisition' }).count(), 0);
    assert.match(await page.locator('main').innerText(), /No equal allocation/);
    await upload('hibid', [
      'synthetic-hibid-embedded-promo.eml', 'synthetic-hibid-embedded-promo.eml',
      'synthetic-hibid-embedded-closing.eml', 'synthetic-hibid-opaque-closing.eml',
    ]);
    await page.goto(base + '/inbox');
    assert.equal(await page.locator('.inbox-card').count(), 5);
    await page.goto(base + '/inbox/messages/6');
    assert.match(await page.locator('main').innerText(), /AUCTION_CLOSING_REMINDER/);
    assert.match(await page.locator('main').innerText(), /CST/);
    const catalog = page.locator('a[href="https://hibid.com/catalog/910000000100"]');
    assert.equal(await catalog.count(), 1);
    assert.equal(await page.locator('a[href*="clicks.auctions.hibid.com"]').count(), 0);
    await page.goto(base + '/inbox/messages/7');
    assert.match(await page.locator('main').innerText(), /NEEDS_REVIEW/);
    assert.match(await page.locator('main').innerText(), /No identifiable item references/);
    await page.setViewportSize({ width: 390, height: 844 });
    const pages = ['/inbox', '/inbox/import', '/inbox/candidates/1', '/inbox/candidates/4',
      '/inbox/messages/1', '/inbox/messages/5', '/inbox/messages/6', '/inbox/messages/7',
      '/invoices/1', '/invoices/2', '/estate-groups', '/listings/1'];
    for (const route of pages) {
      await page.goto(base + route);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
        true, 'Horizontal overflow: ' + route);
    }
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    console.log(JSON.stringify({ desktop: true, mobileViews: pages.length, candidates: 5,
      paidReceipt: '27.00', noMultiItemAllocation: true, opaqueNeedsReview: true,
      browserErrors: errors, externalRequests: external }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
