#!/usr/bin/env node
/* Lead 验收脚本：通过 WebView2 的 CDP 端口（默认 9222）连进真实 app，
   在真 GPU 的页面里测 rAF 帧时间 + 读取真实 DOM/平台状态 + 收集 console 错误。

   前提：app 启动时环境变量 WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS 含
         --remote-debugging-port=9222（tools/verify_runtime.py 已设置）。
   用法：node tools/verify_fps.js [毫秒] [端口]
   输出末行是 JSON，便于脚本拼接。
*/
const { chromium } = require('playwright');

(async () => {
  const ms = Number(process.argv[2] || 10000);
  const port = Number(process.argv[3] || 9222);
  const endpoint = `http://127.0.0.1:${port}`;
  const out = { ok: false, endpoint, ms };

  let browser;
  try {
    browser = await chromium.connectOverCDP(endpoint);
  } catch (e) {
    out.error = `connectOverCDP failed: ${e.message}`;
    console.log(JSON.stringify(out));
    process.exit(2);
  }

  const contexts = browser.contexts();
  const pages = contexts.flatMap(c => c.pages());
  const page = pages.find(p => /index\.html/.test(p.url())) || pages[0];
  if (!page) {
    out.error = 'no page target';
    console.log(JSON.stringify(out));
    process.exit(2);
  }

  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));

  // 现成 DOM 状态
  try {
    out.dom = await page.evaluate(() => {
      const q = s => document.querySelector(s);
      const panel = q('.panel');
      const cs = panel ? getComputedStyle(panel) : null;
      return {
        url: location.href,
        title: document.title,
        rows: document.querySelectorAll('.row').length,
        heroName: q('#hero-name')?.textContent || '',
        subtitle: q('#subtitle')?.textContent || '',
        panelRect: panel ? (r => ({ x: r.x, y: r.y, w: r.width, h: r.height }))(panel.getBoundingClientRect()) : null,
        inner: { w: innerWidth, h: innerHeight, dpr: devicePixelRatio },
        htmlDataset: { ...document.documentElement.dataset },
        backdrop: cs ? cs.backdropFilter : null,
        hasSpecular: !!q('.specular'),
        animations: document.getAnimations().length,
        theme: q('.panel')?.className || '',
        visibility: document.visibilityState,
      };
    });
    out.errors = errors;
  } catch (e) {
    out.error = 'evaluate(dom) failed: ' + e.message;
  }

  // rAF 帧时间（页面可见时才是真实合成节奏）
  try {
    out.frame = await page.evaluate(async (duration) => {
      const gaps = [];
      let last = 0;
      const t0 = performance.now();
      await new Promise(resolve => {
        function tick(t) {
          if (last) gaps.push(t - last);
          last = t;
          if (t - t0 < duration) requestAnimationFrame(tick);
          else resolve();
        }
        requestAnimationFrame(tick);
      });
      gaps.sort((a, b) => a - b);
      const sum = gaps.reduce((a, b) => a + b, 0);
      const p = q => gaps[Math.min(gaps.length - 1, Math.floor(gaps.length * q))] || 0;
      return {
        frames: gaps.length,
        fps: gaps.length ? +(1000 / (sum / gaps.length)).toFixed(1) : 0,
        p50: +p(0.5).toFixed(2),
        p95: +p(0.95).toFixed(2),
        p99: +p(0.99).toFixed(2),
        max: +(gaps[gaps.length - 1] || 0).toFixed(2),
        over20ms: gaps.filter(g => g > 20).length,
      };
    }, ms);
    out.errors = errors;
    out.ok = true;
  } catch (e) {
    out.error = 'rAF measure failed: ' + e.message;
    out.errors = errors;
  }

  // 不要 browser.close()：CDP 连接关闭可能连带关掉 WebView2；直接断开进程
  console.log(JSON.stringify(out));
  process.exit(out.ok ? 0 : 1);
})();
