/* 帧时间采样：空闲 vs 指针持续移动 vs 点击交互
   注意：headless Chromium 走软件渲染（SwiftShader），绝对值偏悲观，
   这里看的是"不同状态之间的相对差异"。
   780×440 仅浏览器预览视口；生产窗口 = 面板 720×380（备选 B），不影响相对性能口径。 */
const { chromium } = require('playwright');
const path = require('path');
const { pathToFileURL } = require('url');

const sleep = ms => new Promise(r => setTimeout(r, ms));

function stats(arr) {
  if (!arr.length) return null;
  const a = [...arr].sort((x, y) => x - y);
  const q = p => a[Math.min(a.length - 1, Math.floor(p * a.length))];
  const sum = a.reduce((s, v) => s + v, 0);
  return {
    n: a.length,
    avg: +(sum / a.length).toFixed(2),
    p50: +q(0.5).toFixed(2),
    p95: +q(0.95).toFixed(2),
    p99: +q(0.99).toFixed(2),
    max: +a[a.length - 1].toFixed(2),
    over20: a.filter(v => v > 20).length,
  };
}

(async () => {
  const browser = await chromium.launch({
    args: ['--enable-gpu', '--use-angle=d3d11', '--ignore-gpu-blocklist'],
  });
  const page = await browser.newPage({ viewport: { width: 780, height: 440 }, deviceScaleFactor: 2 });

  await page.addInitScript(() => {
    window.__frames = [];
    window.__long = [];
    let last = 0;
    const loop = t => {
      if (last) window.__frames.push(t - last);
      last = t;
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
    try {
      new PerformanceObserver(list => {
        for (const e of list.getEntries()) window.__long.push(e.duration);
      }).observe({ entryTypes: ['longtask'] });
    } catch (e) {}
  });

  const url = pathToFileURL(path.resolve(__dirname, '..', 'ui', 'index.html')).href + '?demo';
  await page.goto(url, { waitUntil: 'networkidle' });
  await sleep(1600); // 跳过入场

  const snap = () => page.evaluate(() => {
    const f = window.__frames.slice();
    const l = window.__long.slice();
    window.__frames = [];
    window.__long = [];
    return { frames: f, long: l };
  });

  const box = await (await page.$('#panel')).boundingBox();

  // A. 空闲
  await snap();
  await sleep(3000);
  const idle = await snap();

  // B. 指针在面板内持续移动（触发柔光重绘）
  const t0 = Date.now();
  while (Date.now() - t0 < 3000) {
    for (let i = 0; i <= 10; i++) {
      const x = box.x + 40 + ((i / 10) * (box.width - 80));
      await page.mouse.move(x, box.y + box.height * 0.35, { steps: 2 });
    }
  }
  const moving = await snap();

  // C. 悬停 + 点击交互
  const t1 = Date.now();
  let k = 0;
  while (Date.now() - t1 < 3000) {
    const rows = await page.$$('.row');
    if (rows.length) {
      const b = await rows[k % rows.length].boundingBox();
      await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 6 });
      await page.mouse.down(); await page.mouse.up();
    }
    await sleep(420);
    k++;
  }
  const clicking = await snap();

  console.log(JSON.stringify({
    idle: { frame: stats(idle.frames), longtasks: idle.long.length },
    moving: { frame: stats(moving.frames), longtasks: moving.long.length },
    clicking: { frame: stats(clicking.frames), longtasks: clicking.long.length },
  }, null, 2));

  await browser.close();
})();
