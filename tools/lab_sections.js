const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
const { pathToFileURL } = require('url');
const ROOT = path.resolve(__dirname, '..');
const PAGE = pathToFileURL(path.join(ROOT, 'design', 'motion', 'motion-lab.html')).href;
const OUT = path.join(ROOT, 'design', 'verify', 'motion');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 });
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  await page.goto(PAGE, { waitUntil: 'load' });
  await page.waitForTimeout(1000);
  for (const id of ['clock', 'tokens', 'rules', 'ship']) {
    const el = await page.$('#' + id);
    await el.scrollIntoViewIfNeeded();
    await page.waitForTimeout(350);
    await el.screenshot({ path: path.join(OUT, 'section-' + id + '.png') });
  }
  console.log(errs.length ? 'ERRORS ' + errs.join(' | ') : 'NO_ERRORS');
  await browser.close();
})();
