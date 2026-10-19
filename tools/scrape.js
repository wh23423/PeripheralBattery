/* 抓取公开文档页：渲染后取文本 + 截图，用于确认组件外观与 API */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

(async () => {
  const url = process.argv[2];
  const tag = process.argv[3] || 'doc';
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
  await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 }).catch(() => {});
  await page.waitForTimeout(3500);

  const text = await page.evaluate(() => document.body.innerText);
  const outDir = path.join(__dirname, '..', 'design');
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, `${tag}.txt`), text, 'utf-8');
  await page.screenshot({ path: path.join(outDir, `${tag}.png`), fullPage: true });
  console.log('saved', tag, 'chars:', text.length);
  await browser.close();
})();
