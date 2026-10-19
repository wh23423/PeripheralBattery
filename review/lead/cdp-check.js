const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9223');
  let page = null;
  for (const c of browser.contexts()) for (const p of c.pages()) page = page || p;
  if (!page) throw new Error('no page');
  const errors = [];
  page.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') errors.push(m.type() + ': ' + m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + String(e)));
  await page.waitForTimeout(1500);
  const info = await page.evaluate(() => {
    const panel = document.querySelector('.panel');
    const rows = [...document.querySelectorAll('.row')];
    const cs = panel ? getComputedStyle(panel) : null;
    return {
      title: document.title,
      inner: [innerWidth, innerHeight],
      dpr: devicePixelRatio,
      rows: rows.length,
      rowTexts: rows.slice(0, 12).map(r => r.innerText.replace(/\n/g, ' | ')),
      panelBackdrop: cs ? cs.backdropFilter : null,
      bodyBg: getComputedStyle(document.body).backgroundColor,
      colorMixSupported: CSS.supports('color', 'color-mix(in srgb, red, blue)'),
      supportsHas: CSS.supports('selector(:has(*))'),
      reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
      forcedColors: matchMedia('(forced-colors: active)').matches,
      fontFamily: cs ? cs.fontFamily : null,
    };
  });
  await page.screenshot({ path: 'review/lead/ui-real.png' });
  console.log(JSON.stringify({ info, errors }, null, 2));
  await browser.close();
})().catch(e => { console.error(String(e)); process.exit(1); });