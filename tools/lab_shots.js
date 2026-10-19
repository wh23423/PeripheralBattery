const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
const { pathToFileURL } = require('url');

const ROOT = path.resolve(__dirname, '..');
const OUT = path.join(ROOT, 'design', 'verify', 'motion');
const PAGE = pathToFileURL(path.join(ROOT, 'design', 'motion', 'motion-lab.html')).href;
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 });
  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push('CONSOLE ' + m.text()); });
  page.on('pageerror', e => errors.push('PAGEERROR ' + e.message));

  await page.goto(PAGE, { waitUntil: 'load' });
  await page.waitForTimeout(2200);
  await page.screenshot({ path: path.join(OUT, 'lab-top.png') });

  const wb = await page.$('#workbench');
  if (wb) await wb.screenshot({ path: path.join(OUT, 'lab-workbench.png') });

  // 各场景截图
  const scenes = ['calm', 'charge', 'low', 'handoff', 'list', 'drawer', 'notify', 'offline'];
  for (const id of scenes) {
    await page.click(`[data-scenario="${id}"]`);
    await page.waitForTimeout(id === 'low' ? 1400 : 900);
    const desk = await page.$('.desk');
    await desk.screenshot({ path: path.join(OUT, 'scene-' + id + '.png') });
  }

  // 全页
  await page.screenshot({ path: path.join(OUT, 'lab-full.png'), fullPage: true });

  console.log(errors.length ? errors.join('\n') : 'NO_ERRORS');
  const dims = await page.evaluate(() => ({
    bodyW: document.body.scrollWidth,
    docH: document.documentElement.scrollHeight,
    panel: (() => { const r = document.querySelector('.panel').getBoundingClientRect(); return [r.width, r.height]; })(),
    rail: document.querySelectorAll('.rail-btn').length,
    rows: document.querySelectorAll('#list .row').length,
    anims: document.getAnimations().length
  }));
  console.log(JSON.stringify(dims));
  await browser.close();
})();
