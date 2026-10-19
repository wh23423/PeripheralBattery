const { chromium } = require('playwright');
const path = require('path');
const { pathToFileURL } = require('url');
const ROOT = path.resolve(__dirname, '..');
const PAGE = pathToFileURL(path.join(ROOT, 'design', 'motion', 'motion-lab.html')).href;
const OUT = path.join(ROOT, 'design', 'verify', 'motion');

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    deviceScaleFactor: 2,
    reducedMotion: 'reduce'
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  await page.goto(PAGE, { waitUntil: 'load' });
  await page.waitForTimeout(1600);

  const probe = () => page.evaluate(() => {
    const anims = document.getAnimations();
    const glow = document.querySelector('.ring-lg .breath-normal .glow');
    const alert = document.getElementById('motion-alert');
    return {
      anims: anims.length,
      running: anims.filter(a => a.playState === 'running').length,
      glowDur: getComputedStyle(glow).animationDuration,
      forced: document.documentElement.hasAttribute('data-force-motion'),
      manual: document.documentElement.getAttribute('data-reduced-motion'),
      alertHidden: alert.hidden,
      alertText: document.getElementById('motion-alert-body').textContent.trim().slice(0, 34),
      forceBtn: document.getElementById('btn-force-motion').textContent,
      tour: document.querySelector('.rail-tour-txt').textContent,
      mq: matchMedia('(prefers-reduced-motion: reduce)').matches
    };
  });

  const snap = async (tag, file) => {
    const p = await probe();
    console.log(tag, JSON.stringify(p));
    if (file) await page.screenshot({ path: path.join(OUT, file), clip: { x: 0, y: 0, width: 1440, height: 1000 } });
  };

  await snap('BOOT-AUTOFORCE', 'reduce-autoforce.png');
  await page.waitForTimeout(2600);
  await snap('AFTER-2.6s');

  await page.click('#btn-force-motion');      // 按系统降级
  await page.waitForTimeout(700);
  await snap('SYSTEM-DEGRADED', 'reduce-degraded.png');

  await page.click('#btn-force-motion');      // 再强制播放
  await page.waitForTimeout(900);
  await snap('FORCED-AGAIN');

  await page.click('#opt-reduced');           // 手动降低
  await page.waitForTimeout(500);
  await snap('MANUAL-REDUCE', 'reduce-manual.png');

  await page.click('#btn-force-motion');      // 恢复 → 回到默认强制播放
  await page.waitForTimeout(900);
  await snap('RESTORED');

  console.log('ERRORS', errors.length ? errors : 'none');
  await browser.close();
})();
