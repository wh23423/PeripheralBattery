const { chromium } = require('playwright');
const path = require('path');
const { pathToFileURL } = require('url');
const ROOT = path.resolve(__dirname, '..');
const PAGE = pathToFileURL(path.join(ROOT, 'design', 'motion-preview.html')).href;
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 });
  const errs = [];
  p.on('pageerror', e => errs.push(String(e)));
  await p.goto(PAGE, { waitUntil: 'load' });
  await p.waitForTimeout(1200);
  await p.screenshot({ path: path.join(ROOT, 'design', 'verify', 'motion', 'preview-page.png'), fullPage: true });
  console.log(errs.length ? 'ERRORS ' + errs.join(' | ') : 'NO_ERRORS');
  await b.close();
})();
