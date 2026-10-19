/* 把 ui/index.html 渲染成 PNG，用于设计评审与视觉回归。
   用法：node tools/shot.js [输出路径] [宽] [高] */
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
const { pathToFileURL } = require('url');

(async () => {
  const out = process.argv[2] || path.join(__dirname, '..', 'design', 'panel-v1.png');
  const width = Number(process.argv[3] || 780);
  const height = Number(process.argv[4] || 440);
  const hover = process.argv[5] || '';

  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width, height },
    deviceScaleFactor: 2,
  });

  const url = pathToFileURL(path.resolve(__dirname, '..', 'ui', 'index.html')).href + '?demo';

  await page.goto(url, { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  // 让指针落在玻璃上，带出跟随高光
  await page.mouse.move(width * 0.62, height * 0.34);
  if (hover) {
    const el = await page.$(hover);
    if (el) {
      const box = await el.boundingBox();
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.waitForTimeout(400);
    }
  }
  // 等圆环的过渡画完
  await page.waitForTimeout(900);

  fs.mkdirSync(path.dirname(out), { recursive: true });
  await page.screenshot({ path: out, omitBackground: true });
  await browser.close();
  console.log('saved:', out);
})();
