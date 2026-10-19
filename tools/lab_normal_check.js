const { chromium } = require('playwright');
const path = require('path');
const { pathToFileURL } = require('url');
const ROOT = path.resolve(__dirname, '..');
const PAGE = pathToFileURL(path.join(ROOT, 'design', 'motion', 'motion-lab.html')).href;
const OUT = path.join(ROOT, 'design', 'verify', 'motion');
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  await page.goto(PAGE, { waitUntil: 'load' });
  await page.waitForTimeout(1600);
  const info = await page.evaluate(() => ({
    mq: matchMedia('(prefers-reduced-motion: reduce)').matches,
    anims: document.getAnimations().length,
    alertHidden: document.getElementById('motion-alert').hidden,
    tour: document.querySelector('.rail-tour-txt').textContent,
    glow: getComputedStyle(document.querySelector('.ring-lg .breath-normal .glow')).animationDuration
  }));
  console.log('NORMAL', JSON.stringify(info));
  await page.screenshot({ path: path.join(OUT, 'lab-top-normal.png'), clip: { x: 0, y: 0, width: 1440, height: 1000 } });
  // 等巡演切一次场景，确认在动
  await page.waitForTimeout(4200);
  const active1 = await page.evaluate(() => (document.querySelector('.rail-btn.is-active') || {}).textContent);
  await page.waitForTimeout(2600);
  const active2 = await page.evaluate(() => (document.querySelector('.rail-btn.is-active') || {}).textContent);
  console.log('TOUR ACTIVE', active1, '->', active2);
  // 点场景应停止巡演
  await page.click('[data-scenario="charge"]');
  await page.waitForTimeout(300);
  console.log('AFTER CLICK', JSON.stringify(await page.evaluate(() => ({ tour: document.querySelector('.rail-tour-txt').textContent, active: (document.querySelector('.rail-btn.is-active')||{}).textContent }))));
  await page.screenshot({ path: path.join(OUT, 'lab-workbench-normal.png'), clip: { x: 0, y: 500, width: 1440, height: 500 } });
  console.log('ERRORS', errors.length ? errors : 'none');
  await browser.close();
})();
