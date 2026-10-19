const { chromium } = require('playwright');
const path = require('path');
const { pathToFileURL } = require('url');
const ROOT = path.resolve(__dirname, '..');
const PAGE = pathToFileURL(path.join(ROOT, 'design', 'motion', 'motion-lab.html')).href;
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  await page.goto(PAGE, { waitUntil: 'load' });
  await page.waitForTimeout(1000);
  await page.click('#btn-force-motion');
  // 强制播放后让巡演跑完一整轮（约 25s），期间每 2s 采样动画数
  const samples = [];
  for (let i = 0; i < 13; i++) {
    await page.waitForTimeout(2000);
    samples.push(await page.evaluate(() => ({
      t: Math.round(performance.now() / 1000),
      anims: document.getAnimations().length,
      running: document.getAnimations().filter(a => a.playState === 'running').length,
      active: (document.querySelector('.rail-btn.is-active') || {}).textContent
    })));
  }
  console.log(JSON.stringify(samples));
  console.log('ERRORS', errors.length ? errors : 'none');
  await browser.close();
})();
