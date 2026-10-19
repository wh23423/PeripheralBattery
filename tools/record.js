/* 录一段真实操作演示：指针柔光、行悬停、点击涟漪、换主卡、按钮发光、入场动画
   780×440 仅浏览器预览视口；生产窗口 = 面板 720×380（备选 B），录制画布沿用预览尺寸。 */
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
const { pathToFileURL } = require('url');

const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const dir = path.join(__dirname, '..', 'design', 'rec');
  fs.rmSync(dir, { recursive: true, force: true });
  fs.mkdirSync(dir, { recursive: true });

  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 780, height: 440 },
    deviceScaleFactor: 2,
    recordVideo: { dir, size: { width: 780, height: 440 } },
  });
  const page = await context.newPage();
  const url = pathToFileURL(path.resolve(__dirname, '..', 'ui', 'index.html')).href + '?demo';

  await page.goto(url, { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  await sleep(1500); // 入场动画播完

  const box = async sel => {
    const el = await page.$(sel);
    return el ? el.boundingBox() : null;
  };
  const center = b => ({ x: b.x + b.width / 2, y: b.y + b.height / 2 });
  const glide = async (to, steps = 22, ms = 40) => {
    const { x, y } = typeof to === 'object' && 'x' in to ? to : center(await box(to));
    await page.mouse.move(x, y, { steps });
    await sleep(ms);
  };

  // 1. 控件簇：刷新发光 -> 点击
  await glide('#btn-refresh');
  await sleep(350);
  await page.mouse.down(); await page.mouse.up();
  await sleep(800);

  // 2. 固定按钮：点亮
  await glide('#btn-pin');
  await sleep(250);
  await page.mouse.down(); await page.mouse.up();
  await sleep(600);

  // 3. 指针滑过玻璃，柔光跟随
  await page.mouse.move(180, 300, { steps: 26 });
  await sleep(260);
  await page.mouse.move(520, 240, { steps: 30 });
  await sleep(420);

  // 4. 悬停充电中的手柄 -> 点击换主卡（绿色呼吸）
  const pad = await box('[data-key="gamepad"]');
  await page.mouse.move(pad.x + pad.width * 0.5, pad.y + pad.height / 2, { steps: 18 });
  await sleep(450);
  await page.mouse.down(); await page.mouse.up();
  await sleep(1900);

  // 5. 换到键盘（92%）
  const kb = await box('[data-key="keyboard"]');
  await page.mouse.move(kb.x + kb.width * 0.5, kb.y + kb.height / 2, { steps: 16 });
  await sleep(380);
  await page.mouse.down(); await page.mouse.up();
  await sleep(1200);

  // 6. 换到鼠标（68%）
  const mouse = await box('[data-key="mouse"]');
  await page.mouse.move(mouse.x + mouse.width * 0.55, mouse.y + mouse.height / 2, { steps: 16 });
  await sleep(380);
  await page.mouse.down(); await page.mouse.up();
  await sleep(1200);

  // 7. 点空白玻璃：涟漪
  await page.mouse.move(200, 370, { steps: 14 });
  await sleep(300);
  await page.mouse.down(); await page.mouse.up();
  await sleep(700);

  // 8. 悬停主按钮：反色发光 -> 点击
  const all = await box('.all-btn');
  await page.mouse.move(all.x + all.width / 2, all.y + all.height / 2, { steps: 18 });
  await sleep(650);
  await page.mouse.down(); await page.mouse.up();
  await sleep(600);

  // 9. 收尾：指针移开
  await page.mouse.move(760, 430, { steps: 20 });
  await sleep(700);

  await context.close();
  await browser.close();

  const files = fs.readdirSync(dir).filter(f => f.endsWith('.webm'));
  console.log('video:', path.join(dir, files[0]));
})();
