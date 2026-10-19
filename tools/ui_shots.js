/* 生成前端评审截图（不影响 tools/shot.js）
   用法：node tools/ui_shots.js
   输出：design/verify/ui-prod-*.png（生产模式，无 backdrop-filter）
        design/verify/ui-dev-*.png（?effects=css，保留 CSS 毛玻璃用于评审）
   视口固定 780×440 DIP（面板 720×380 + 四周 30px 安全边）。 */
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
const { pathToFileURL } = require('url');

const ROOT = path.resolve(__dirname, '..');
const OUT = path.join(ROOT, 'design', 'verify');
const PAGE = pathToFileURL(path.join(ROOT, 'ui', 'index.html')).href;
const W = 780, H = 440;

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function settle(page) {
  await page.waitForFunction(() => window.__pbDebug && window.__pbDebug.renders > 0, null, { timeout: 5000 });
  await page.evaluate(() => document.fonts && document.fonts.ready);
  await sleep(1300); // 跳过入场动画
}

async function shot(page, name) {
  fs.mkdirSync(OUT, { recursive: true });
  const file = path.join(OUT, name);
  await page.screenshot({ path: file, omitBackground: true });
  const size = fs.statSync(file).size;
  console.log('saved:', path.relative(ROOT, file), size + 'B');
}

(async () => {
  const browser = await chromium.launch();
  const errors = [];

  async function newPage(query) {
    const page = await browser.newPage({
      viewport: { width: W, height: H },
      deviceScaleFactor: 2
    });
    page.on('pageerror', e => errors.push('pageerror: ' + e.message));
    page.on('console', msg => { if (msg.type() === 'error') errors.push('console: ' + msg.text()); });
    await page.goto(PAGE + query, { waitUntil: 'networkidle' });
    await settle(page);
    return page;
  }

  /* 生产模式（默认无参数 → 无 backdrop-filter） */
  let page = await newPage('?demo');
  await page.mouse.move(W * 0.62, H * 0.34);
  await sleep(260);
  await shot(page, 'ui-prod-demo.png');
  await page.close();

  page = await newPage('?mock&freeze');
  await page.mouse.move(W * 0.62, H * 0.34);
  await sleep(260);
  await shot(page, 'ui-prod-mock.png');
  await page.close();

  /* 开发/评审模式（?effects=css 保留 CSS 毛玻璃） */
  page = await newPage('?demo&effects=css');
  await page.mouse.move(W * 0.62, H * 0.34);
  await sleep(260);
  await shot(page, 'ui-dev-demo.png');

  const row = await page.$('.row:not([hidden])');
  if (row) {
    const box = await row.boundingBox();
    await page.mouse.move(box.x + box.width * 0.62, box.y + box.height / 2);
    await sleep(320);
    await shot(page, 'ui-dev-hover.png');
  }

  await page.click('#btn-settings');
  await sleep(420);
  await shot(page, 'ui-dev-settings.png');
  await page.close();

  page = await newPage('?mock&freeze&effects=css');
  await page.mouse.move(W * 0.62, H * 0.34);
  await sleep(260);
  await shot(page, 'ui-dev-mock.png');
  await page.close();

  /* 隐藏模拟：跳过入场后再隐藏，截图是"静止的最后一帧" */
  page = await newPage('?mock&freeze&effects=css');
  await page.evaluate(() => window.__pbMock.visibility(true));
  await page.waitForFunction(() => window.__pbDebug && window.__pbDebug.hidden === true, null, { timeout: 5000 });
  await sleep(220);
  await shot(page, 'ui-dev-hidden.png');
  await page.close();

  await browser.close();

  if (errors.length) {
    console.error('页面错误：\n' + errors.join('\n'));
    process.exitCode = 1;
  } else {
    console.log('no console errors');
  }
})();
