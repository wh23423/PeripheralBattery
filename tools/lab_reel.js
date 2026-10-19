const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
const { pathToFileURL } = require('url');

const ROOT = path.resolve(__dirname, '..');
const PAGE = pathToFileURL(path.join(ROOT, 'design', 'motion', 'motion-lab.html')).href;
const OUT = path.join(ROOT, 'design', 'verify', 'motion');
const VID = path.join(OUT, 'reel-raw');
fs.rmSync(VID, { recursive: true, force: true });
fs.mkdirSync(VID, { recursive: true });

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    recordVideo: { dir: VID, size: { width: 1440, height: 1000 } }
  });
  const page = await context.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  await page.goto(PAGE, { waitUntil: 'load' });
  await sleep(900);
  // 让桌面框靠近视口顶部，录像裁剪坐标才固定
  await page.evaluate(() => {
    const f = document.querySelector('.desk-frame').getBoundingClientRect();
    window.scrollBy(0, f.top - 40);
  });
  await sleep(900);

  const marks = {};
  const click = async (id, label) => {
    marks[label] = Date.now();
    await page.click(`.rail-btn[data-scenario="${id}"]`);
  };

  marks.start = Date.now();
  await sleep(2200);                       // 常态呼吸
  await click('handoff', 'handoff'); await sleep(1900);
  await click('list', 'list'); await sleep(1700);
  await click('charge', 'charge'); await sleep(2100);
  await click('low', 'low'); await sleep(3800);
  await click('drawer', 'drawer'); await sleep(1700);
  await click('hover', 'hover'); await sleep(1500);
  await click('notify', 'notify'); await sleep(1900);
  await click('calm', 'calm'); await sleep(1600);
  marks.end = Date.now();

  const video = page.video();
  await context.close();
  await browser.close();
  const p = await video.path();
  const meta = { marks, fileName: path.basename(p) };
  fs.writeFileSync(path.join(OUT, 'reel-marks.json'), JSON.stringify(meta, null, 2));
  console.log(JSON.stringify(meta, null, 2));
  console.log(errs.length ? 'ERRORS ' + errs.join(' | ') : 'NO_ERRORS');
  console.log('elapsed', marks.end - marks.start, 'ms');
})();
