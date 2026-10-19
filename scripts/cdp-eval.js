#!/usr/bin/env node
/**
 * 测试辅助：通过 CDP 连接运行中的 Tauri WebView2，执行一段 JS 表达式并打印结果。
 * 用法：WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222 启动 app 后：
 *   node scripts/cdp-eval.js "window.__TAURI__.core.invoke('collector_status')"
 */
const { chromium } = require('playwright');

(async () => {
  const expression = process.argv.slice(2).join(' ');
  if (!expression) {
    throw new Error('usage: node scripts/cdp-eval.js "<javascript expression>"');
  }
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  let page = null;
  for (const context of browser.contexts()) {
    for (const p of context.pages()) {
      if (!page) page = p;
    }
  }
  if (!page) throw new Error('CDP: no page found');
  const result = await page.evaluate(expression);
  console.log(JSON.stringify({ ok: true, result }));
  await browser.close();
})().catch((e) => {
  console.error(JSON.stringify({ ok: false, error: String(e) }));
  process.exit(1);
});
