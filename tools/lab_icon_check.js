const { chromium } = require('playwright');
const path = require('path');
const { pathToFileURL } = require('url');
const ROOT = path.resolve(__dirname, '..');
const PAGE = pathToFileURL(path.join(ROOT, 'design', 'motion', 'motion-lab.html')).href;
const OUT = path.join(ROOT, 'design', 'verify', 'motion');
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2, reducedMotion: 'reduce' });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  await page.goto(PAGE, { waitUntil: 'load' });
  await page.waitForTimeout(900);
  // 强制播放，保证动效路径也走一遍
  await page.evaluate(() => document.getElementById('opt-force').click());
  await page.waitForTimeout(400);
  const panel = await page.$('#panel');
  const box = await panel.boundingBox();
  // 控件簇 + 底栏按钮特写
  await page.screenshot({ path: path.join(OUT, 'icon-controls.png'), clip: { x: box.x + box.width - 300, y: box.y + box.height - 70, width: 300, height: 60 } });
  await page.screenshot({ path: path.join(OUT, 'icon-settings.png'), clip: { x: box.x + box.width - 300, y: box.y + 6, width: 300, height: 40 } });
  const before = await page.$$eval('#list .row', els => els.map(e => e.dataset.key));
  await page.click('#btn-all');
  await page.waitForTimeout(650);
  const after = await page.$$eval('#list .row', els => els.map(e => e.dataset.key));
  const drawerOpen = await page.$eval('#drawer', d => d.classList.contains('is-open'));
  await page.screenshot({ path: path.join(OUT, 'icon-all-open.png'), clip: { x: box.x + box.width - 620, y: box.y + box.height - 330, width: 620, height: 300 } });
  await page.click('#btn-all');
  await page.waitForTimeout(650);
  const back = await page.$$eval('#list .row', els => els.map(e => e.dataset.key));
  console.log(JSON.stringify({ before, after, drawerOpen, back, errors: errors.length ? errors : 'none' }));
  await browser.close();
})();
