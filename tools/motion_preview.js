#!/usr/bin/env node
'use strict';

/**
 * 录制动效提案页 → 逐帧 PNG 序列 + timings.json（CDP screencast）。
 *
 * 用法：node tools/motion_preview.js [--out dir] [--seconds 10.4]
 *
 * 为什么用 screencast：Playwright 的 screenshot() 每帧要等字体/稳定，成本
 * ~150ms；Page.startScreencast 直接吐合成器帧（约 20–30fps），带时间戳，
 * 更适合录动效。为什么不是 recordVideo：只出 webm，环境里没有 ffmpeg。
 * GIF/WebP 由 tools/encode_motion.py（Pillow）编码。
 */

const path = require('path');
const fs = require('fs');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const PAGE = path.join(ROOT, 'design', 'motion-preview.html');
const args = process.argv.slice(2);
function arg(name, fallback) {
  const i = args.indexOf('--' + name);
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback;
}

const OUT_DIR = path.resolve(ROOT, arg('out', 'design/_motion_frames'));
const DURATION_MS = Math.round(Number(arg('seconds', '10.6')) * 1000);

(async () => {
  fs.rmSync(OUT_DIR, { recursive: true, force: true });
  fs.mkdirSync(OUT_DIR, { recursive: true });

  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 900, height: 600 },
    deviceScaleFactor: 1,
  });

  page.on('pageerror', (e) => console.error('[pageerror]', e.message));
  page.on('console', (m) => {
    if (m.type() === 'error') console.error('[console]', m.text());
  });

  await page.goto('file:///' + PAGE.replace(/\\/g, '/'));
  await page.evaluate(async () => {
    if (document.fonts && document.fonts.ready) await document.fonts.ready;
  });
  await page.waitForTimeout(300);

  // 进入录制态：隐藏工具栏、舞台铺满 900×600、重置为浅色壁纸
  await page.evaluate(() => window.__setRecording(true));
  await page.waitForTimeout(250);

  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Page.enable');

  const frames = [];
  cdp.on('Page.screencastFrame', async ({ data, metadata, sessionId }) => {
    frames.push({ t: metadata.timestamp * 1000, data });
    try {
      await cdp.send('Page.screencastFrameAck', { sessionId });
    } catch (_) {
      /* 录制结束时 ack 可能落在已关闭的会话上，忽略 */
    }
  });

  await cdp.send('Page.startScreencast', {
    format: 'png',
    maxWidth: 900,
    maxHeight: 600,
    everyNthFrame: 1,
  });

  const started = Date.now();
  page.evaluate(() => window.__playDemo());

  await page.waitForTimeout(DURATION_MS);
  // 演示里最后还有收尾动画，多给一点
  await page.waitForTimeout(400);

  await cdp.send('Page.stopScreencast');
  await browser.close();

  if (!frames.length) throw new Error('screencast produced no frames');
  frames.sort((a, b) => a.t - b.t);

  // 以第一帧为 0，写盘并生成 timings.json
  const t0 = frames[0].t;
  const manifest = [];
  frames.forEach((f, idx) => {
    const file = 'f' + String(idx).padStart(4, '0') + '.png';
    fs.writeFileSync(path.join(OUT_DIR, file), Buffer.from(f.data, 'base64'));
    manifest.push({ file, t: Math.round(f.t - t0) });
  });
  fs.writeFileSync(
    path.join(OUT_DIR, 'timings.json'),
    JSON.stringify({ frames: manifest }, null, 0),
  );

  const span = manifest[manifest.length - 1].t;
  console.log(
    `captured ${frames.length} frames over ${span}ms (${(frames.length / (span / 1000)).toFixed(1)} fps) → ${OUT_DIR}`,
  );
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
