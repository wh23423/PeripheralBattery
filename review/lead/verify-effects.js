const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
const { pathToFileURL } = require('url');
const ROOT = path.resolve(__dirname, '..', '..');
const OUT = path.join(ROOT, 'review', 'lead');
const PAGE = pathToFileURL(path.join(ROOT, 'ui', 'index.html')).href;
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 780, height: 440 }, deviceScaleFactor: 1.25 });
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  await page.goto(PAGE + '?mock&freeze&effects=css', { waitUntil: 'networkidle' });
  await page.waitForFunction(() => window.__pbDebug && window.__pbDebug.renders > 0, null, { timeout: 5000 });
  await page.evaluate(() => document.fonts && document.fonts.ready);
  await sleep(1500);
  await page.mouse.move(5, 5);
  await sleep(200);
  await page.screenshot({ path: path.join(OUT, 'effects-a.png'), omitBackground: true });
  await sleep(3500);
  await page.screenshot({ path: path.join(OUT, 'effects-b.png'), omitBackground: true });

  const anims = await page.evaluate(() => document.getAnimations().map(a => ({
    name: a.animationName || (a.effect && a.effect.getKeyframes ? 'css' : 'waapi'),
    state: a.playState
  })));
  const hero = await page.$('.hero-card');
  const hb = await hero.boundingBox();
  await page.mouse.move(hb.x + hb.width * 0.45, hb.y + hb.height * 0.4);
  await sleep(450);
  const heroInfo = await page.evaluate(() => {
    const c = document.querySelector('.hero-card');
    return { mx: c.style.getPropertyValue('--mx'), my: c.style.getPropertyValue('--my'),
             transform: getComputedStyle(c).transform,
             beforeOpacity: getComputedStyle(c, '::before').opacity };
  });
  await page.screenshot({ path: path.join(OUT, 'effects-hero-hover.png'), omitBackground: true });

  const row = await page.$('.row:not([hidden])');
  const rb = await row.boundingBox();
  await page.mouse.move(rb.x + rb.width * 0.45, rb.y + rb.height * 0.5);
  await sleep(450);
  const rowInfo = await page.evaluate(() => {
    const r = document.querySelector('.row:not([hidden])');
    const ring = r.querySelector('.ring-sm svg');
    return { mx: r.style.getPropertyValue('--mx'), my: r.style.getPropertyValue('--my'),
             rowTransform: getComputedStyle(r).transform,
             ringTransform: ring ? getComputedStyle(ring).transform : null };
  });
  await page.screenshot({ path: path.join(OUT, 'effects-row-hover.png'), omitBackground: true });

  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.mouse.move(5, 5);
  await page.mouse.move(hb.x + 10, hb.y + 10);
  await sleep(300);
  const reduced = await page.evaluate(() => {
    const c = document.querySelector('.hero-card');
    const anims = document.getAnimations();
    return { reduced: matchMedia('(prefers-reduced-motion: reduce)').matches,
             mx: c.style.getPropertyValue('--mx'),
             animCount: anims.length,
             durations: anims.slice(0, 4).map(a => getComputedStyle(a.effect.target).animationDuration) };
  });
  await page.screenshot({ path: path.join(OUT, 'effects-reduced.png'), omitBackground: true });

  console.log(JSON.stringify({ anims, heroInfo, rowInfo, reduced, errors }, null, 2));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });