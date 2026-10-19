const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  await page.goto('https://pro.reactbits.dev/docs/components/satin-ridges', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2500);

  // 切到 Code 标签
  const tab = page.getByText('Code', { exact: true }).first();
  if (await tab.count()) {
    await tab.click().catch(() => {});
    await page.waitForTimeout(1500);
  }
  const text = await page.evaluate(() => document.body.innerText);
  const out = path.join(__dirname, '..', 'design', 'satin-code.txt');
  fs.writeFileSync(out, text, 'utf-8');
  await page.screenshot({ path: path.join(__dirname, '..', 'design', 'satin-code.png') });
  console.log('ok', text.length);
  await browser.close();
})();
