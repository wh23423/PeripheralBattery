const { chromium } = require('playwright');
const path = require('path');
const { pathToFileURL } = require('url');
const ROOT = path.resolve(__dirname, '..');
const PAGE = pathToFileURL(path.join(ROOT, 'design', 'motion', 'motion-lab.html')).href;
const OUT = path.join(ROOT, 'design', 'verify', 'motion');
(async () => {
  const b = await chromium.launch();
  const page = await b.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 });
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  await page.goto(PAGE, { waitUntil: 'load' });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(OUT, 'lab-full.png'), fullPage: true });
  await page.screenshot({ path: path.join(OUT, 'lab-top.png') });
  console.log(errs.length ? 'ERRORS ' + errs.join(' | ') : 'NO_ERRORS');
  await b.close();
})();
