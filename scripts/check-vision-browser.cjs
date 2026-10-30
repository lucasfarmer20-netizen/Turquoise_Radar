// Prepare a NEW /tmp DB with prepare-vision-browser.py, then serve it on private port 8005.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
(async () => {
  const base = 'http://127.0.0.1:8005';
  const images = '/tmp/turquoise-vision-browser-photos/';
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium',
    headless: true, args: ['--no-sandbox'] });
  const errors = [], external = [];
  async function context(options) {
    const c = await browser.newContext(options);
    await c.route('**/*', route => {
      if (new URL(route.request().url()).origin !== base) {
        external.push(route.request().url()); return route.abort();
      }
      return route.continue();
    });
    c.on('page', p => {
      p.on('pageerror', e => errors.push(e.message));
      p.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
    });
    return c;
  }
  try {
    const desktop = await context({ viewport: { width: 1440, height: 1000 } });
    const p = await desktop.newPage();
    await p.goto(base + '/listings/1');
    await p.getByRole('link', { name: 'Review photographs', exact: true }).click();
    await p.waitForURL(/\/vision\/cases\/1$/);
    const upload = p.locator('form.vision-upload');
    await upload.locator('[name="files"]').setInputFiles([0,1,2,3].map(i => images+i+'.png'));
    await upload.getByRole('button', { name: 'Upload private photos' }).click();
    assert.equal(await p.locator('.vision-photo').count(), 4);
    await p.locator('.vision-photo a[data-zoom]').first().click();
    assert.equal(await p.locator('#photo-zoom').evaluate(el => el.open), true);
    await p.getByRole('button', { name: 'Close preview' }).click();
    const quick = () => p.locator('.vision-analyze[data-provider="fixture"][data-stage="QUICK"]');
    await quick().getByRole('button', { name: 'Analyze Jewelry · free synthetic fixture' }).click();
    await p.waitForURL(/run=1/);
    assert.match(await p.locator('main').innerText(), /VISUAL OPPORTUNITY/);
    assert.match(await p.locator('main').innerText(), /NOT A READING OF THE UPLOADED IMAGES/);
    const correction = p.locator('form[action="/vision/runs/1/correct"]').first();
    await correction.locator('[name="action"]').selectOption('INCORRECT');
    await correction.locator('[name="note"]').fill('SYNTHETIC correction; inspect actual close-up.');
    await correction.getByRole('button', { name: 'Record finding correction' }).click();
    assert.match(await p.locator('main').innerText(), /SYNTHETIC correction/);
    await quick().getByRole('button', { name: 'Analyze Jewelry · free synthetic fixture' }).click();
    await p.waitForURL(/cached=yes/);
    await p.getByText('Separate synthetic deeper comparison', { exact: true }).click();
    const deep = p.locator('.vision-analyze[data-provider="fixture"][data-stage="DEEP"]');
    await deep.locator('[name="deep_confirm"]').check();
    await deep.getByRole('button', { name: 'Analyze Deeper (fixture) · free synthetic fixture' }).click();
    await p.waitForURL(/run=2/);
    await p.goto(base + '/vision/cases/1?run=1&run=2');
    assert.equal(await p.locator('.vision-result').count(), 2);
    assert.match(await p.locator('main').innerText(), /Differences to investigate/);
    const back = p.locator('.vision-photo').nth(1);
    await back.locator('summary').click();
    await back.locator('input[type="file"]').setInputFiles(images+'closeup.png');
    await back.getByRole('button', { name: 'Replace photo', exact: true }).click();
    assert.equal(await p.locator('.vision-photo').count(), 4);
    await p.locator('.vision-photo').first().getByRole('button', { name: 'Remove from active photos' }).click();
    assert.equal(await p.locator('.vision-photo').count(), 3);
    assert.equal(await p.locator('.vision-analyze[data-provider="anthropic"][data-stage="QUICK"] button').isDisabled(), true);
    await p.goto(base + '/listings/1');
    assert.match(await p.locator('main').innerText(), /UNKNOWN/);
    await p.goto(base + '/vision/for/listing/2');
    const lotUpload = p.locator('form.vision-upload');
    await lotUpload.locator('[name="files"]').setInputFiles([images+'0.png', images+'1.png']);
    await lotUpload.getByRole('button', { name: 'Upload private photos' }).click();
    await quick().getByRole('button', { name: 'Analyze Jewelry · free synthetic fixture' }).click();
    await p.waitForURL(/run=3/);
    const subitem = p.locator('form[action="/vision/runs/3/correct"]').filter({ has: p.locator('input[name="sub_item_id"]') });
    await subitem.locator('[name="note"]').fill('SYNTHETIC foreground detail needs a separate close-up.');
    await subitem.getByRole('button', { name: 'Mark sub-item for review' }).click();
    assert.match(await p.locator('main').innerText(), /SUBITEM_REVIEW/);
    await p.goto(base+'/vision/settings');
    assert.match(await p.locator('main').innerText(), /Paid vision is disabled/);
    assert.equal(await p.locator('input[name="ANTHROPIC_API_KEY"]').count(), 0);
    await p.goto(base+'/vision/cases/1?run=1&run=2');
    await p.screenshot({ path: '/tmp/turquoise-vision-desktop.png', fullPage: true });
    const mobile = await context({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true,
      userAgent: 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36' });
    const m = await mobile.newPage();
    const pages = ['/vision','/vision/settings','/vision/cases/1','/vision/cases/1?run=1&run=2',
      '/vision/cases/2','/listings/1','/inbox','/discovery','/queue'];
    for (const route of pages) {
      await m.goto(base+route);
      assert.equal(await m.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'Overflow '+route);
    }
    await m.goto(base+'/vision/cases/2');
    await m.screenshot({ path: '/tmp/turquoise-vision-mobile.png', fullPage: true });
    assert.deepEqual(errors, []); assert.deepEqual(external, []);
    console.log(JSON.stringify({ desktop:true, androidSizedViews:pages.length, fourPhotos:true,
      zoom:true, cached:true, separateDeepRun:true, ownerCorrection:true, replacementAndRemoval:true,
      mixedSubitemReview:true, paidDisabled:true, browserErrors:errors, externalRequests:external }));
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
