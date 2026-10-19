/* ============================================================
   tools/ui_smoke.js — 前端 Playwright 冒烟测试（一键运行）
   用法：node tools/ui_smoke.js
   失败退出码非 0；截图输出 design/verify/ui-*.png。

   为了真实覆盖 Tauri 的 CSP（style-src 'self'），测试用本地 HTTP
   服务器给 ui/ 加上与 BUILD_PLAN §5.3 相同的 CSP 头再加载页面。
   ============================================================ */
'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const UI_DIR = path.join(ROOT, 'ui');
const SHOT_DIR = path.join(ROOT, 'design', 'verify');

const CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src ipc: http://ipc.localhost";
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.json': 'application/json'
};

const W = 780;
const H = 440;
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* ---------------------------------------------------------- 断言工具 */

const failures = [];
let passed = 0;

function ok(cond, msg) {
  if (!cond) throw new Error(msg || '断言失败');
}

function eq(actual, expected, msg) {
  if (actual !== expected) {
    throw new Error((msg || '值不相等') + '：期望 ' + JSON.stringify(expected) + '，实际 ' + JSON.stringify(actual));
  }
}

function includes(haystack, needle, msg) {
  if (String(haystack).indexOf(needle) < 0) {
    throw new Error((msg || '缺少文本') + '：' + JSON.stringify(String(haystack).slice(0, 220)) + ' 中找不到 ' + JSON.stringify(needle));
  }
}

async function test(name, fn) {
  try {
    await fn();
    passed++;
    console.log('  \u2713 ' + name);
  } catch (err) {
    failures.push({ name: name, message: err && err.message || String(err) });
    console.log('  \u2717 ' + name);
    console.log('      ' + (err && err.message || err));
  }
}

/* ---------------------------------------------------------- 静态文件服务器（带 CSP） */

function startServer() {
  return new Promise(resolve => {
    const server = http.createServer((req, res) => {
      const urlPath = decodeURIComponent((req.url || '/').split('?')[0]);
      const rel = urlPath === '/' ? '/index.html' : urlPath;
      if (rel === '/favicon.ico') { res.writeHead(204); res.end(); return; }
      const file = path.normalize(path.join(UI_DIR, rel));
      if (!file.startsWith(UI_DIR)) { res.writeHead(403); res.end('forbidden'); return; }
      fs.readFile(file, (err, buf) => {
        if (err) { res.writeHead(404); res.end('not found'); return; }
        res.writeHead(200, {
          'Content-Type': MIME[path.extname(file).toLowerCase()] || 'application/octet-stream',
          'Content-Security-Policy': CSP,
          'Cache-Control': 'no-store'
        });
        res.end(buf);
      });
    });
    server.listen(0, '127.0.0.1', () => {
      resolve({ server: server, base: 'http://127.0.0.1:' + server.address().port + '/index.html' });
    });
  });
}

/* ---------------------------------------------------------- 页面工具 */

let browser = null;
let baseUrl = '';
const pageErrors = [];

/* 默认用 780×440 浏览器预览视口（.stage 720×380 居中，留出外阴影空间）；
   传 vpWidth/vpHeight 可模拟其它视口，如 720×380 的生产窗口（窗口=面板，备选 B）。 */
async function openPage(query, vpWidth, vpHeight) {
  const page = await browser.newPage({
    viewport: { width: vpWidth || W, height: vpHeight || H },
    deviceScaleFactor: 2
  });
  const pageName = query;
  page.on('pageerror', err => pageErrors.push({ page: pageName, kind: 'pageerror', text: String(err && err.message || err) }));
  page.on('console', msg => {
    if (msg.type() === 'error') pageErrors.push({ page: pageName, kind: 'console', text: msg.text() });
  });
  await page.goto(baseUrl + query, { waitUntil: 'networkidle' });
  await page.waitForFunction(() => window.__pbDebug && window.__pbDebug.renders > 0, null, { timeout: 8000 });
  return page;
}

/* A12 契约测试：模拟 Tauri 外壳（bridge.hasTauri=true，不启动真实应用）。
   注入最小的 window.__TAURI__：invoke 只记录调用，event.listen 保存 handler。 */
async function openTauriLikePage(query) {
  const page = await browser.newPage({
    viewport: { width: W, height: H },
    deviceScaleFactor: 2
  });
  await page.addInitScript(() => {
    window.__tauriCalls = [];
    window.__TAURI__ = {
      core: {
        invoke: async (cmd, args) => {
          window.__tauriCalls.push({ cmd: cmd, args: args || null });
          return null;
        }
      },
      event: {
        listen: async () => () => {}
      }
    };
  });
  page.on('pageerror', err => pageErrors.push({ page: 'tauri-like:' + query, kind: 'pageerror', text: String(err && err.message || err) }));
  page.on('console', msg => {
    if (msg.type() === 'error') pageErrors.push({ page: 'tauri-like:' + query, kind: 'console', text: msg.text() });
  });
  await page.goto(baseUrl + query, { waitUntil: 'networkidle' });
  await page.waitForFunction(() => window.__pbDebug && window.__pbDebug.renders > 0, null, { timeout: 8000 });
  return page;
}

async function waitRenders(page, n) {
  const target = n == null ? (await page.evaluate(() => window.__pbDebug.renders)) + 1 : n;
  await page.waitForFunction(count => window.__pbDebug.renders >= count, target, { timeout: 5000 });
}

async function waitIntroDone(page) {
  await page.waitForFunction(() => !document.body.classList.contains('intro'), null, { timeout: 5000 });
}

async function screenshot(page, name) {
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const file = path.join(SHOT_DIR, name);
  await page.screenshot({ path: file, omitBackground: true });
  const size = fs.statSync(file).size;
  ok(size > 20000, '截图疑似为空：' + name + ' (' + size + 'B)');
  console.log('      screenshot: design/verify/' + name + ' (' + size + 'B)');
  return file;
}

function rect(page, selector) {
  return page.evaluate(sel => {
    const node = document.querySelector(sel);
    if (!node) return null;
    const r = node.getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height, right: r.right, bottom: r.bottom };
  }, selector);
}

/* ---------------------------------------------------------- 测试主体 */

(async () => {
  const srv = await startServer();
  baseUrl = srv.base;
  browser = await chromium.launch();
  console.log('ui smoke · ' + baseUrl + ' · CSP 已启用');

  /* ============ 1. ?demo&effects=css：渲染 / 布局 / 截图 ============ */
  await test('G1 ?demo&effects=css 截图非空、4+ 行、主卡与列表无溢出', async () => {
    const page = await openPage('?demo&effects=css');
    try {
      await page.mouse.move(W * 0.62, H * 0.34);
      await sleep(900);
      await screenshot(page, 'ui-dev-demo.png');

      const rows = await page.$$eval('.row:not([hidden])', nodes => nodes.length);
      ok(rows >= 4, '列表行数不足 4，实际 ' + rows);

      const layout = await page.evaluate(() => {
        const panel = document.getElementById('panel').getBoundingClientRect();
        const list = document.getElementById('list');
        const hero = document.getElementById('hero').getBoundingClientRect();
        const side = document.querySelector('.side').getBoundingClientRect();
        const bodyCs = getComputedStyle(document.body);
        return {
          vw: window.innerWidth,
          vh: window.innerHeight,
          panel: { x: panel.x, y: panel.y, w: panel.width, h: panel.height, right: panel.right, bottom: panel.bottom },
          listScroll: list.scrollHeight,
          listClient: list.clientHeight,
          hero: { x: hero.x, y: hero.y, right: hero.right, bottom: hero.bottom },
          side: { x: side.x, y: side.y, right: side.right, bottom: side.bottom },
          bodyPadding: [bodyCs.paddingTop, bodyCs.paddingRight, bodyCs.paddingBottom, bodyCs.paddingLeft],
          panelRadius: getComputedStyle(document.getElementById('panel')).borderTopLeftRadius,
          docOverflowX: document.documentElement.scrollWidth > window.innerWidth,
          docOverflowY: document.documentElement.scrollHeight > window.innerHeight
        };
      });
      /* 备选 B（REVIEW-REPORT §10.3）：生产窗口 = 面板 720×380，四周无安全边。
         本用例用 780×440 浏览器预览视口：body 是 grid 居中容器，.stage 应正好
         居中（左右/上下各留 30px 预览余量，只为能看到外阴影）。 */
      ok(Math.abs(layout.panel.x - (W - 720) / 2) <= 1, '预览视口下面板未水平居中：' + layout.panel.x);
      ok(Math.abs(layout.panel.y - (H - 380) / 2) <= 1, '预览视口下面板未垂直居中：' + layout.panel.y);
      eq(Math.round(layout.panel.w), 720, '面板宽度');
      eq(Math.round(layout.panel.h), 380, '面板高度');
      ok(layout.panel.right <= W - 29 && layout.panel.bottom <= H - 29, '面板撑出 780×440 预览视口');
      /* 备选 B：不能再靠 body padding 造安全边；圆角 12px 贴近 DWM 窗口圆角 */
      eq(layout.bodyPadding.join(','), '0px,0px,0px,0px', 'body 仍有 padding（备选 B 应无安全边）');
      eq(layout.panelRadius, '12px', '面板圆角应为 12px（贴近 Win11 DWM 窗口圆角）');
      ok(!layout.docOverflowX && !layout.docOverflowY, '文档出现滚动溢出');
      ok(layout.listScroll <= layout.listClient + 1, 'demo 列表出现纵向溢出：' + layout.listScroll + ' > ' + layout.listClient);
      ok(layout.hero.right <= layout.panel.right && layout.hero.bottom <= layout.panel.bottom, '主卡溢出面板');
      ok(layout.side.right <= layout.panel.right && layout.side.bottom <= layout.panel.bottom, '列表区溢出面板');

      /* ?effects=css 必须真的保留 CSS 毛玻璃 */
      const filters = await page.evaluate(() => ({
        panel: getComputedStyle(document.getElementById('panel')).backdropFilter,
        controls: getComputedStyle(document.getElementById('controls')).backdropFilter,
        hero: getComputedStyle(document.querySelector('.hero-card')).backdropFilter,
        row: getComputedStyle(document.querySelector('.row')).backdropFilter
      }));
      ok(filters.panel !== 'none', 'effects=css 时面板应有 backdrop-filter');
      ok(filters.controls !== 'none', 'effects=css 时控件簇应有 backdrop-filter');
      ok(filters.hero !== 'none', 'effects=css 时主卡应有 backdrop-filter');
      ok(filters.row !== 'none', 'effects=css 时列表行应有 backdrop-filter');
    } finally {
      await page.close();
    }
  });

  /* ============ 2. ?mock：null / approx / charging / low / alert ============ */
  await test('G2 ?mock 的 null/approx/charging/low 文案与 alert toast', async () => {
    const page = await openPage('?mock&freeze');
    try {
      const rows = await page.$$eval('.row:not([hidden])', n => n.length);
      eq(rows, 4, 'mock 列表行数（5 台设备，主卡 1 台）');

      /* level=null：必须显示未知，不能显示 0% */
      const nullRow = await page.$('[data-key="logi:046d:c092:unit-77"]');
      ok(!!nullRow, '找不到 null 电量设备行');
      const nullInfo = await nullRow.evaluate(node => ({
        text: node.textContent,
        level: node.querySelector('.row-level').textContent,
        levelClass: node.querySelector('.row-level').className
      }));
      eq(nullInfo.level, '未知', 'null 电量行右侧文案');
      includes(nullInfo.text, '未知', 'null 电量行文案');
      ok(nullInfo.text.indexOf('0%') < 0, 'null 电量被渲染成了 0%');
      includes(nullInfo.levelClass, 'is-unknown', 'null 电量标记 class');

      /* approx：档位设备展示 approx 文案（协议 §1） */
      const approxRow = await page.$('[data-key="steelseries:1038:1720:unit-5"]');
      ok(!!approxRow, '找不到 approx 设备行');
      const approxInfo = await approxRow.evaluate(node => ({
        text: node.textContent,
        right: node.querySelector('.row-level').textContent,
        ring: node.querySelector('.ring').className
      }));
      includes(approxInfo.text, '约 55%（中）', 'approx 文案');
      eq(approxInfo.right, '55%', 'approx 设备右侧电量');
      ok(approxInfo.ring.indexOf('state-low') < 0, '55% 不应被判低电');

      /* charging：圆环状态 class + 文案 */
      const chargeInfo = await page.$eval('[data-key="xinput:045e:0b00:unit-1"]', node => ({
        ring: node.querySelector('.ring').className,
        text: node.textContent
      }));
      includes(chargeInfo.ring, 'state-charging', '充电设备圆环 class');
      includes(chargeInfo.text, '充电中', '充电中文案');

      /* low：主卡（最低电量 12%）红色 + 建议充电 */
      const heroInfo = await page.evaluate(() => ({
        ringClass: document.getElementById('hero-ring').className,
        alertHidden: document.getElementById('hero-alert').hidden,
        alertText: document.getElementById('hero-alert').textContent,
        panelLow: document.getElementById('panel').classList.contains('is-low')
      }));
      includes(heroInfo.ringClass, 'state-low', '主卡低电 class');
      ok(!heroInfo.alertHidden, '低电主卡应显示告警标签');
      includes(heroInfo.alertText, '建议充电', '告警标签文案');
      ok(heroInfo.panelLow, '低电时面板应加 is-low');

      /* collector alert → toast */
      await page.evaluate(() => {
        window.__pbMock.alert('low', 'logi:046d:c547:unit-12', 12, 'Arctis Nova 7 电量低，建议充电');
      });
      await page.waitForSelector('.toast', { state: 'visible', timeout: 3000 });
      const toastText = await page.$eval('.toast', node => node.textContent);
      includes(toastText, '电量低', '告警 toast 文案');

      /* 副标题：设备数 + HH:MM 更新时间 */
      const subtitle = await page.$eval('#subtitle', node => node.textContent);
      ok(/5 台设备 · \d{2}:\d{2} 更新/.test(subtitle), '副标题设备数/更新时间格式错误：' + subtitle);

      /* transport 枚举 → 展示文案；left_s → 人类可读 */
      const viperText = await page.$eval('[data-key="razer:1532:0099:unit-3"]', node => node.textContent);
      includes(viperText, '2.4 GHz', '2.4g transport 映射');
      includes(viperText, '约 18 小时', 'left_s 人类可读');
      const unknownText = await page.$eval('[data-key="logi:046d:c092:unit-77"]', node => node.textContent);
      includes(unknownText, '未知连接', 'unknown transport 映射');
      const chargeText = await page.$eval('[data-key="xinput:045e:0b00:unit-1"]', node => node.textContent);
      includes(chargeText, '蓝牙', 'bluetooth transport 映射');
      eq(await page.$eval('#hero-caption', node => node.textContent), '约 1 小时 48 分', 'left_s 人类可读（主卡）');

      await screenshot(page, 'ui-prod-mock.png');
    } finally {
      await page.close();
    }
  });

  /* ============ 3. keyed diff：节点复用 / 悬停 / 焦点 ============ */
  await test('G3 20 次数据更新后行节点复用、悬停与焦点保持', async () => {
    const page = await openPage('?mock&freeze&effects=css');
    try {
      await page.waitForFunction(() => window.__pbDebug.rowCount === 4, null, { timeout: 3000 });
      const second = page.locator('.row:not([hidden])').nth(1);
      const box = await second.boundingBox();
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await sleep(120);

      await page.evaluate(() => {
        window.__rowsBefore = Array.from(document.querySelectorAll('.row:not([hidden])'));
        window.__hoverRow = window.__rowsBefore[1];
        window.__focusRow = window.__rowsBefore[0];
        window.__createsBefore = window.__pbDebug.rowCreates;
        window.__removesBefore = window.__pbDebug.rowRemoves;
        window.__levelBefore = window.__hoverRow.querySelector('.row-level').textContent;
        window.__focusRow.focus();
      });

      const hoverBefore = await page.evaluate(() => window.__hoverRow.matches(':hover'));
      ok(hoverBefore, '数据更新前悬停状态未生效');

      await page.evaluate(() => {
        const key = window.__hoverRow.dataset.key;
        window.__pbMock.stressUpdates(key, 20);
      });
      await sleep(160);

      const after = await page.evaluate(() => {
        const rowsNow = Array.from(document.querySelectorAll('.row:not([hidden])'));
        return {
          sameCount: rowsNow.length,
          sameNodes: window.__rowsBefore.every((node, i) => node === rowsNow[i]),
          nodeCount: window.__rowsBefore.length,
          creates: window.__pbDebug.rowCreates,
          createsBefore: window.__createsBefore,
          removes: window.__pbDebug.rowRemoves,
          removesBefore: window.__removesBefore,
          hoverKept: window.__hoverRow.matches(':hover'),
          focusKept: document.activeElement === window.__focusRow,
          hoverConnected: window.__hoverRow.isConnected,
          levelChanged: window.__hoverRow.querySelector('.row-level').textContent !== window.__levelBefore,
          renders: window.__pbDebug.renders
        };
      });
      eq(after.sameCount, 4, '更新后行数变化');
      ok(after.sameNodes, '数据更新后行 DOM 节点被替换（发生了整列表重建）');
      eq(after.creates, after.createsBefore, '数据更新不应创建新行节点');
      eq(after.removes, after.removesBefore, '数据更新不应删除行节点');
      ok(after.hoverConnected, '悬停行被移除');
      ok(after.hoverKept, '数据更新后悬停状态丢失');
      ok(after.focusKept, '数据更新后键盘焦点丢失');
      ok(after.levelChanged, '数据更新没有生效（电量文案未变化）');
      await screenshot(page, 'ui-dev-hover.png');
    } finally {
      await page.close();
    }
  });

  /* ============ 4. session 重置 / replaces morph / seq 乱序 ============ */
  await test('G4 session 重置不重播 intro、replaces 原地 morph、seq 乱序丢弃', async () => {
    const page = await openPage('?mock&freeze&effects=css');
    try {
      await waitIntroDone(page);
      const introPlays1 = await page.evaluate(() => window.__pbDebug.introPlays);
      eq(introPlays1, 1, '入场动画应只播 1 次');

      /* session 变化：全量重置但不重播 intro */
      const rowsBefore = await page.$$eval('.row:not([hidden])', n => n.length);
      await page.evaluate(() => { window.__pbMock.sessionReset('smoke-session-2'); });
      await sleep(120);
      const sessionInfo = await page.evaluate(() => ({
        sessions: window.__pbDebug.sessions,
        intro: document.body.classList.contains('intro'),
        introPlays: window.__pbDebug.introPlays,
        rows: document.querySelectorAll('.row:not([hidden])').length,
        session: window.__pbDebug.session
      }));
      eq(sessionInfo.sessions, 2, 'session 变化应触发一次全量重置');
      ok(!sessionInfo.intro, 'session 变化后 body.intro 被重新加上（会重播入场动画）');
      eq(sessionInfo.introPlays, 1, 'intro 播放次数被增加');
      eq(sessionInfo.rows, rowsBefore, 'session 重置后行数异常');
      eq(sessionInfo.session, 'smoke-session-2', 'session id 未更新');

      /* replaces：key 迁移 → 同一 DOM 节点 morph，不 delete+insert */
      const morphInfo = await page.evaluate(() => {
        const oldKey = window.__pbDebug.rowKeys[0];
        const node = document.querySelector('[data-key="' + oldKey + '"]');
        window.__morphOldKey = oldKey;
        window.__morphNode = node;
        window.__createsBefore = window.__pbDebug.rowCreates;
        window.__removesBefore = window.__pbDebug.rowRemoves;
        window.__pbMock.device({
          key: 'morph:new:key:1',
          name: 'Renamed Device',
          level: 40,
          approx: '',
          left_s: 3600,
          charging: false,
          online: true,
          kind: 'mouse',
          transport: '2.4g'
        }, oldKey);
        return { oldKey: oldKey, creates: window.__pbDebug.rowCreates, removes: window.__pbDebug.rowRemoves };
      });
      await sleep(120);
      const morphAfter = await page.evaluate(() => {
        const newNode = document.querySelector('[data-key="morph:new:key:1"]');
        return {
          sameNode: newNode === window.__morphNode,
          connected: !!newNode && newNode.isConnected,
          oldGone: document.querySelector('[data-key="' + window.__morphOldKey + '"]') === null,
          name: newNode ? newNode.querySelector('.row-name').textContent : '',
          creates: window.__pbDebug.rowCreates,
          removes: window.__pbDebug.rowRemoves,
          opacity: newNode ? getComputedStyle(newNode).opacity : '0',
          rows: document.querySelectorAll('.row:not([hidden])').length
        };
      });
      ok(morphAfter.sameNode && morphAfter.connected, 'replaces 后行 DOM 节点被替换（不是 morph）');
      ok(morphAfter.oldGone, 'replaces 后旧 key 行仍然存在');
      eq(morphAfter.name, 'Renamed Device', 'replaces 后行文案未更新');
      eq(morphAfter.creates, morphInfo.creates, 'replaces 不应创建新节点');
      eq(morphAfter.removes, morphInfo.removes, 'replaces 不应删除节点');
      eq(morphAfter.opacity, '1', 'replaces 后行透明度异常（疑似闪烁）');
      eq(morphAfter.rows, rowsBefore, 'replaces 后行数变化');

      /* seq 乱序：比当前 seq 旧的事件必须丢弃 */
      const dropped = await page.evaluate(() => {
        const seq = window.__pbDebug.seq;
        const key = window.__pbDebug.rowKeys[0];
        const row = document.querySelector('[data-key="' + key + '"]');
        const before = row.querySelector('.row-level').textContent;
        const beforeDropped = window.__pbDebug.dropped;
        window.__pbMock.emit({
          t: 'device',
          session: 'smoke-session-2',
          seq: Math.max(0, seq - 3),
          device: {
            key: key, name: 'Stale Name', level: 3, approx: '', left_s: null,
            charging: false, online: true, kind: 'mouse', transport: '2.4g'
          }
        });
        return {
          levelBefore: before,
          levelAfter: row.querySelector('.row-level').textContent,
          nameAfter: row.querySelector('.row-name').textContent,
          droppedBefore: beforeDropped,
          droppedAfter: window.__pbDebug.dropped
        };
      });
      eq(dropped.droppedAfter, dropped.droppedBefore + 1, '乱序 seq 未被丢弃');
      eq(dropped.levelAfter, dropped.levelBefore, '乱序事件改写了电量');
      ok(dropped.nameAfter !== 'Stale Name', '乱序事件改写了设备名');
      await screenshot(page, 'ui-dev-session.png');
    } finally {
      await page.close();
    }
  });

  /* ============ 5. 生产模式零 backdrop-filter / 柔光结构 ============ */
  await test('G5 生产模式 computedStyle 无 backdrop-filter，?effects=css 有', async () => {
    const prod = await openPage('?demo');
    try {
      await waitIntroDone(prod);
      const prodInfo = await prod.evaluate(() => {
        const all = Array.from(document.querySelectorAll('*'));
        const withFilter = all.filter(node => {
          const cs = getComputedStyle(node);
          return (cs.backdropFilter && cs.backdropFilter !== 'none') ||
            (cs.webkitBackdropFilter && cs.webkitBackdropFilter !== 'none');
        }).map(node => node.id || node.className || node.tagName);
        const sel = s => {
          const node = document.querySelector(s);
          return node ? getComputedStyle(node).backdropFilter : 'missing';
        };
        const specular = document.getElementById('specular');
        const ring = document.getElementById('hero-ring');
        return {
          filterCount: withFilter.length,
          filters: withFilter.slice(0, 8),
          panel: sel('#panel'),
          controls: sel('#controls'),
          hero: sel('.hero-card'),
          row: sel('.row'),
          effects: document.documentElement.dataset.effects || '',
          specularInsidePanel: !!(specular && specular.closest('#panel')),
          specularBlend: specular ? getComputedStyle(specular).mixBlendMode : 'missing',
          specularWillChange: specular ? getComputedStyle(specular).willChange : 'missing',
          specularParent: specular && specular.parentElement ? specular.parentElement.id : '',
          panelWillChange: getComputedStyle(document.getElementById('panel')).willChange,
          heroRing: ring ? ring.className : ''
        };
      });
      eq(prodInfo.filterCount, 0, '生产模式仍有元素带 backdrop-filter：' + JSON.stringify(prodInfo.filters));
      eq(prodInfo.panel, 'none', '生产模式面板 backdrop-filter');
      eq(prodInfo.controls, 'none', '生产模式控件簇 backdrop-filter');
      eq(prodInfo.hero, 'none', '生产模式主卡 backdrop-filter');
      eq(prodInfo.row, 'none', '生产模式列表行 backdrop-filter');
      eq(prodInfo.effects, '', '生产模式不应设置 data-effects');
      ok(prodInfo.specularParent === 'glow-layer', '指针柔光不在独立的 glow-layer 层');
      ok(!prodInfo.specularInsidePanel, '指针柔光仍在 .panel（backdrop-filter 祖先）内');
      eq(prodInfo.specularBlend, 'normal', '指针柔光不应有 mix-blend-mode');
      includes(prodInfo.specularWillChange, 'auto', '柔光 will-change 不应常驻');
      eq(prodInfo.panelWillChange, 'auto', '入场结束后面板 will-change 应被移除');
      await screenshot(prod, 'ui-prod-demo.png');
    } finally {
      await prod.close();
    }

    const dev = await openPage('?demo&effects=css');
    try {
      const devFilters = await dev.evaluate(() => {
        const sel = s => getComputedStyle(document.querySelector(s)).backdropFilter;
        return {
          effects: document.documentElement.dataset.effects || '',
          panel: sel('#panel'),
          controls: sel('#controls'),
          hero: sel('.hero-card'),
          row: sel('.row')
        };
      });
      eq(devFilters.effects, 'css', 'data-effects 未按 ?effects=css 设置');
      ok(devFilters.panel !== 'none', 'effects=css 面板无 backdrop-filter');
      ok(devFilters.controls !== 'none', 'effects=css 控件簇无 backdrop-filter');
      ok(devFilters.hero !== 'none', 'effects=css 主卡无 backdrop-filter');
      ok(devFilters.row !== 'none', 'effects=css 列表行无 backdrop-filter');
    } finally {
      await dev.close();
    }
  });

  /* ============ 6. 隐藏四步前端部分 ============ */
  await test('G6 隐藏后动画全 paused、rAF 停、DOM 不更新，恢复后渲染最新快照', async () => {
    const page = await openPage('?mock&freeze&effects=css&hide');
    try {
      await page.waitForFunction(() => window.__pbDebug.hidden === true, null, { timeout: 5000 });
      await sleep(180);

      const hiddenInfo = await page.evaluate(() => {
        const anims = document.getAnimations();
        return {
          bodyHidden: document.body.classList.contains('is-hidden'),
          animationCount: anims.length,
          running: anims.filter(a => a.playState === 'running').length,
          paused: anims.filter(a => a.playState === 'paused').length,
          rafActive: window.__pbDebug.rafActive,
          rafTicks: window.__pbDebug.rafTicks,
          timers: window.__pbDebug.timersActive,
          renders: window.__pbDebug.renders,
          skips: window.__pbDebug.renderSkipsHidden,
          domWrites: window.__pbDebug.domWritesWhileHidden
        };
      });
      ok(hiddenInfo.bodyHidden, '隐藏后 body.is-hidden 未设置');
      ok(hiddenInfo.animationCount > 0, '隐藏时没有可暂停的动画，用例不可信');
      eq(hiddenInfo.running, 0, '隐藏后仍有动画在运行（WAAPI 未暂停）');
      ok(hiddenInfo.paused > 0, '隐藏后没有 paused 动画');
      eq(hiddenInfo.rafActive, false, '隐藏后 rAF 仍在运行');
      eq(hiddenInfo.timers, 0, '隐藏后 UI 定时器未清理');

      /* 隐藏期间推 30 次数据：DOM 必须保持不动 */
      const target = await page.evaluate(() => {
        const key = window.__pbDebug.rowKeys[1];
        const row = document.querySelector('[data-key="' + key + '"]');
        window.__hiddenRow = row;
        window.__hiddenKey = key;
        window.__hiddenText = row.querySelector('.row-level').textContent;
        window.__hiddenRenders = window.__pbDebug.renders;
        window.__hiddenRaf = window.__pbDebug.rafTicks;
        for (let i = 0; i < 30; i++) {
          window.__pbMock.setLevel(key, 30 + i);
        }
        return { key: key, text: window.__hiddenText, renders: window.__hiddenRenders };
      });
      await sleep(260);

      const duringHidden = await page.evaluate(() => ({
        text: window.__hiddenRow.querySelector('.row-level').textContent,
        textBefore: window.__hiddenText,
        renders: window.__pbDebug.renders,
        rendersBefore: window.__hiddenRenders,
        skips: window.__pbDebug.renderSkipsHidden,
        domWrites: window.__pbDebug.domWritesWhileHidden,
        rafTicks: window.__pbDebug.rafTicks,
        rafBefore: window.__hiddenRaf,
        connected: window.__hiddenRow.isConnected
      }));
      eq(duringHidden.text, duringHidden.textBefore, '隐藏期间 DOM 被写入（行文案已变化）');
      eq(duringHidden.renders, duringHidden.rendersBefore, '隐藏期间仍执行了渲染');
      ok(duringHidden.skips >= 30, '隐藏期间未记录到跳过的渲染次数：' + duringHidden.skips);
      eq(duringHidden.domWrites, 0, '隐藏期间发生 DOM 写入：' + duringHidden.domWrites);
      eq(duringHidden.rafTicks, duringHidden.rafBefore, '隐藏期间 rAF 仍在跑');
      ok(duringHidden.connected, '隐藏期间行节点被移除');
      await screenshot(page, 'ui-dev-hidden.png');

      /* 恢复：先用最后快照渲染一帧，再恢复动画 */
      await page.evaluate(() => { window.__pbMock.visibility(false); });
      await page.waitForFunction(() => window.__pbDebug.hidden === false, null, { timeout: 3000 });
      await sleep(160);
      const resumed = await page.evaluate(() => {
        const anims = document.getAnimations();
        return {
          text: window.__hiddenRow.querySelector('.row-level').textContent,
          renders: window.__pbDebug.renders,
          rendersHidden: window.__hiddenRenders,
          bodyHidden: document.body.classList.contains('is-hidden'),
          running: anims.filter(a => a.playState === 'running').length,
          sameNode: document.querySelector('[data-key="' + window.__hiddenKey + '"]') === window.__hiddenRow
        };
      });
      ok(!resumed.bodyHidden, '恢复后 body.is-hidden 未移除');
      ok(resumed.renders > resumed.rendersHidden, '恢复后没有渲染最新快照');
      eq(resumed.text, '59%', '恢复后未渲染最后一份快照：' + resumed.text);
      ok(resumed.sameNode, '恢复后行节点被替换');
      ok(resumed.running > 0, '恢复后动画没有继续播放');
    } finally {
      await page.close();
    }
  });

  /* ============ 7. 控制台 / CSP / 内联样式 ============ */
  await test('G7 无控制台错误、无 CSP 违规、无内联 style/script', async () => {
    /* 静态源码检查 */
    const html = fs.readFileSync(path.join(UI_DIR, 'index.html'), 'utf8');
    ok(!/\sstyle\s*=/i.test(html), 'index.html 存在内联 style 属性');
    ok(!/<style[\s>]/i.test(html), 'index.html 存在内联 <style>');
    ok(!/\son[a-z]+\s*=/i.test(html), 'index.html 存在内联事件属性');
    ok(!/javascript:/i.test(html), 'index.html 存在 javascript: URL');
    ok(!/<script(?![^>]*\bsrc=)/i.test(html), 'index.html 存在内联 <script>');

    const scriptSrcs = Array.from(html.matchAll(/<script[^>]*\bsrc="([^"]+)"/g)).map(m => m[1]);
    eq(scriptSrcs.join(','), 'bridge.js,app.js', 'script 引用顺序错误');

    const appJs = fs.readFileSync(path.join(UI_DIR, 'app.js'), 'utf8');
    const bridgeJs = fs.readFileSync(path.join(UI_DIR, 'bridge.js'), 'utf8');
    ok(!/\.cssText\s*=/.test(appJs) && !/\.cssText\s*=/.test(bridgeJs), 'JS 中使用 cssText 写内联样式');
    ok(!/setAttribute\(\s*['"]style['"]/.test(appJs) && !/setAttribute\(\s*['"]style['"]/.test(bridgeJs), 'JS 用 setAttribute 写 style 属性');
    ok(!/innerHTML\s*=\s*[`'"][^`'"]*style\s*=/.test(appJs) && !/innerHTML\s*=\s*[`'"][^`'"]*style\s*=/.test(bridgeJs), 'innerHTML 字符串里带 style=');

    /* CSS：backdrop-filter 只允许出现在 data-effects / reduced-transparency 作用域 */
    const css = fs.readFileSync(path.join(UI_DIR, 'styles.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    const lines = css.split(/\r?\n/);
    let selector = '';
    const badFilters = [];
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      if (trimmed.indexOf('backdrop-filter') >= 0 && trimmed.indexOf('{') < 0) {
        const scope = selector + ' ' + trimmed;
        if (!/data-effects|prefers-reduced-transparency/.test(scope)) badFilters.push(trimmed);
        continue;
      }
      if (trimmed.indexOf('{') >= 0) {
        const head = trimmed.slice(0, trimmed.indexOf('{'));
        if (head) selector = head;
      }
    }
    eq(badFilters.length, 0, '生产作用域出现 backdrop-filter：' + JSON.stringify(badFilters));

    /* 没有页面级错误 / CSP 违规（所有已打开页面） */
    const cspIssues = pageErrors.filter(e => /content security policy|refused to|violates the following/i.test(e.text));
    const otherErrors = pageErrors.filter(e => !/content security policy|refused to|violates the following/i.test(e.text));
    eq(cspIssues.length, 0, 'CSP 违规：' + JSON.stringify(cspIssues.slice(0, 4)));
    eq(otherErrors.length, 0, '控制台错误：' + JSON.stringify(otherErrors.slice(0, 4)));
  });

  /* ============ 附加 A. 托盘状态同步 ============ */
  await test('A1 set_tray_state：最低电量设备 / 全离线 online:false / level=null', async () => {
    const page = await openPage('?mock&freeze');
    try {
      const tray1 = await page.evaluate(() => window.__pbDebug.lastTray);
      ok(tray1 && tray1.online === true, '托盘状态未同步 online');
      eq(tray1.level, 12, '托盘应取最低电量设备（12%）');
      eq(tray1.kind, 'headset', '托盘 kind');
      const calls = await page.evaluate(() => window.__pbDebug.trayCalls.length);
      ok(calls >= 1, '没有调用 set_tray_state');

      await page.evaluate(() => { window.__pbMock.allOffline(); });
      await sleep(120);
      const offline = await page.evaluate(() => ({
        offlineRows: document.querySelectorAll('.row.is-offline:not([hidden])').length,
        rowsText: Array.from(document.querySelectorAll('.row:not([hidden])')).map(n => n.textContent).join('|'),
        liveText: document.getElementById('live-text').textContent,
        subtitle: document.getElementById('subtitle').textContent,
        heroCaption: document.getElementById('hero-caption').textContent
      }));
      ok(offline.offlineRows > 0, '离线设备行未加 is-offline');
      includes(offline.rowsText, '离线', '离线设备行文案');
      eq(offline.liveText, '离线', '全部离线时在线徽标文案');
      includes(offline.subtitle, '0 台设备', '全部离线时副标题设备数');
      eq(offline.heroCaption, '设备离线', '离线主卡文案');
      const tray2 = await page.evaluate(() => window.__pbDebug.lastTray);
      eq(tray2.online, false, '全离线时托盘 online 应为 false');
      eq(tray2.level, null, '全离线时托盘 level 应为 null');

      /* level=null 的在线设备：允许 level:null 直接透传 */
      const trayNull = await page.evaluate(() => {
        window.__pbMock.tick([{
          key: 'only:unknown:1', name: 'Unknown Device', level: null, approx: '',
          left_s: null, charging: false, online: true, kind: 'mouse', transport: 'unknown'
        }]);
        return window.__pbDebug.lastTray;
      });
      eq(trayNull.level, null, '在线但电量未知的设备应传 level=null');
      eq(trayNull.online, true, '在线设备托盘 online 应为 true');
    } finally {
      await page.close();
    }
  });

  /* ============ 附加 B. 设置抽屉 / 键盘 / 持久化 ============ */
  await test('A2 设置抽屉：键盘可操作、分组结构、阈值唯一来源 collector、UI 偏好持久化', async () => {
    const page = await openPage('?mock&freeze&effects=css');
    try {
      await page.evaluate(() => localStorage.removeItem('pb.settings.v1'));
      await page.reload({ waitUntil: 'networkidle' });
      await page.waitForFunction(() => window.__pbDebug && window.__pbDebug.renders > 0, null, { timeout: 8000 });
      await waitIntroDone(page);

      /* 键盘焦点到设置按钮 → Enter 打开 */
      await page.focus('#btn-settings');
      await page.keyboard.press('Enter');
      await page.waitForSelector('#drawer.is-open', { timeout: 2000 });
      const opened = await page.evaluate(() => ({
        expanded: document.getElementById('btn-settings').getAttribute('aria-expanded'),
        ariaHidden: document.getElementById('drawer').getAttribute('aria-hidden'),
        inert: document.getElementById('drawer').hasAttribute('inert'),
        focus: document.activeElement && document.activeElement.id
      }));
      eq(opened.expanded, 'true', '按钮 aria-expanded 未更新');
      eq(opened.ariaHidden, 'false', '抽屉 aria-hidden 未更新');
      ok(!opened.inert, '打开后抽屉仍有 inert');
      eq(opened.focus, 'set-autostart', '打开抽屉后焦点未进入第一个设置项（S-01 启动组）');

      /* S-01：抽屉分组结构 / 控件语义 / 新设置项存在性 */
      const groups = await page.evaluate(() => {
        const groups = Array.from(document.querySelectorAll('#drawer fieldset.group'));
        const legends = groups.map(n => (n.querySelector(':scope > legend') || {}).textContent || '');
        const box = document.getElementById('set-autostart');
        const label = box && box.closest('label');
        const op = document.getElementById('set-opacity');
        const opOut = document.getElementById('set-opacity-out');
        const th = document.getElementById('set-threshold');
        const thOut = document.getElementById('set-threshold-out');
        return {
          count: groups.length,
          legends: legends.map(t => t.trim()),
          autostartType: box ? box.type : '',
          autostartLabel: label ? label.textContent.trim() : '',
          opacityType: op ? op.type : '',
          opacityMin: op ? op.min : '',
          opacityMax: op ? op.max : '',
          opacityStep: op ? op.step : '',
          opacityValue: op ? op.value : '',
          opacityOut: opOut ? opOut.textContent : '',
          opacityOutFor: opOut ? opOut.getAttribute('for') : '',
          thresholdType: th ? th.type : '',
          thresholdMin: th ? th.min : '',
          thresholdMax: th ? th.max : '',
          thresholdStep: th ? th.step : '',
          thresholdValue: th ? th.value : '',
          thresholdOut: thOut ? thOut.textContent : '',
          thresholdOutFor: thOut ? thOut.getAttribute('for') : '',
          notifyLow: document.getElementById('set-notify-low').checked,
          notifyFull: document.getElementById('set-notify-full').checked,
          pinned: !!document.getElementById('set-pinned'),
          veil: !!document.getElementById('low-veil'),
          statusState: document.getElementById('autostart-status').getAttribute('data-state')
        };
      });
      eq(groups.count, 5, '抽屉应分为 5 个设置分组（启动/外观/提醒/面板/关于）');
      eq(groups.legends.join(','), '启动,外观,提醒,面板,关于', '设置分组 legend 文案/顺序');
      eq(groups.autostartType, 'checkbox', '自启开关底层应为原生 checkbox');
      includes(groups.autostartLabel, '开机静默启动', '自启开关 label 文案');
      eq(groups.opacityType, 'range', '不透明度应为 range 滑块');
      eq(groups.opacityMin, '50', '不透明度下限应为 50');
      eq(groups.opacityMax, '100', '不透明度上限应为 100');
      eq(groups.opacityStep, '1', '不透明度步长应为 1');
      eq(groups.opacityValue, '60', '不透明度默认值应为 60');
      eq(groups.opacityOut, '60%', '不透明度 output 初始值');
      eq(groups.opacityOutFor, 'set-opacity', '不透明度 output 未用 for 关联滑块');
      /* F-04 / P2-4：阈值范围与 collector 协议一致（0–100，不再 5–50） */
      eq(groups.thresholdType, 'range', '低电阈值应为 range 滑块');
      eq(groups.thresholdMin, '0', '低电阈值下限应为协议下限 0');
      eq(groups.thresholdMax, '100', '低电阈值上限应为协议上限 100');
      eq(groups.thresholdStep, '1', '低电阈值步长应为 1');
      eq(groups.thresholdValue, '20', '低电阈值初值应来自 collector hello.settings.low=20');
      eq(groups.thresholdOut, '20%', '低电阈值 output 初始值');
      eq(groups.thresholdOutFor, 'set-threshold', '低电阈值 output 未用 for 关联滑块');
      eq(groups.notifyLow, true, '低电系统通知默认勾选');
      eq(groups.notifyFull, true, '充满系统通知默认勾选');
      ok(groups.pinned, '固定面板开关缺失');
      ok(groups.veil, '低电卡片预烘焙高光层 #low-veil 缺失');
      eq(groups.statusState, 'off', '自启状态初始应为未启用');

      /* 抽屉是覆盖层，不能改变 720×380 面板外框/在预览视口中的居中位置 */
      const panelWhileOpen = await page.evaluate(() => {
        const r = document.getElementById('panel').getBoundingClientRect();
        return { x: r.x, y: r.y, w: r.width, h: r.height };
      });
      eq(Math.round(panelWhileOpen.w), 720, '打开设置后面板宽度被改变');
      eq(Math.round(panelWhileOpen.h), 380, '打开设置后面板高度被改变');
      eq(Math.round(panelWhileOpen.x), (W - 720) / 2, '打开设置后面板在预览视口中的位置被改变');
      eq(Math.round(panelWhileOpen.y), (H - 380) / 2, '打开设置后面板在预览视口中的位置被改变');

      /* F-04：调整低电阈值 → 唯一来源是 collector。input 乐观更新 UI，
         change 发 collector_send {"t":"set","field":"low","value":N}（无 key），
         不再写 localStorage（localStorage 只保留 reducedMotion / pinned 等 UI 偏好）。 */
      await page.evaluate(() => {
        const input = document.getElementById('set-threshold');
        input.value = '32';
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      });
      await sleep(80);
      const thresholdInfo = await page.evaluate(() => {
        const sent = window.__pbBridge.calls.filter(c => c.cmd === 'collector_send');
        const last = sent.length ? JSON.parse(sent[sent.length - 1].args.line) : null;
        const stored = JSON.parse(localStorage.getItem('pb.settings.v1') || '{}');
        return {
          debug: window.__pbDebug.settings.lowThreshold,
          collectorLow: window.__pbBridge.collectorLow,
          out: document.getElementById('set-threshold-out').textContent,
          sent: last,
          storedLow: Object.prototype.hasOwnProperty.call(stored, 'lowThreshold')
        };
      });
      eq(thresholdInfo.debug, 32, '低电阈值未生效（debug.settings.lowThreshold）');
      eq(thresholdInfo.collectorLow, 32, 'collector 未收到低电阈值（collector set 未生效）');
      eq(thresholdInfo.out, '32%', '低电阈值输出未更新');
      ok(thresholdInfo.sent, 'change 后未发送 collector_send');
      eq(thresholdInfo.sent.t, 'set', 'collector 消息类型应为 set');
      eq(thresholdInfo.sent.field, 'low', 'collector set 字段应为 low');
      eq(thresholdInfo.sent.value, 32, 'collector set 值应为 32');
      eq(Object.prototype.hasOwnProperty.call(thresholdInfo.sent, 'key'), false, 'collector set 不应带 key');
      eq(thresholdInfo.storedLow, false, '低电阈值不应再写入 localStorage（唯一来源 = collector）');

      /* 降低动效勾选 */
      await page.evaluate(() => {
        const box = document.getElementById('set-reduced');
        box.checked = true;
        box.dispatchEvent(new Event('change', { bubbles: true }));
      });
      const reduced = await page.evaluate(() => ({
        dataset: document.documentElement.dataset.reducedMotion,
        stored: JSON.parse(localStorage.getItem('pb.settings.v1') || '{}').reducedMotion
      }));
      eq(reduced.dataset, 'true', '降低动效未写到 html[data-reduced-motion]');
      eq(reduced.stored, true, '降低动效未持久化');

      await screenshot(page, 'ui-dev-settings.png');

      /* Escape 关闭，焦点回到设置按钮 */
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => !document.getElementById('drawer').classList.contains('is-open'), null, { timeout: 2000 });
      const closed = await page.evaluate(() => ({
        expanded: document.getElementById('btn-settings').getAttribute('aria-expanded'),
        focus: document.activeElement && document.activeElement.id
      }));
      eq(closed.expanded, 'false', '关闭后 aria-expanded 未复位');
      eq(closed.focus, 'btn-settings', '关闭后焦点未回到设置按钮');

      /* 刷新后：阈值回到 collector 的 hello.settings.low（mock 默认 20），
         降低动效是 UI 偏好仍从 localStorage 恢复（F-04 后阈值不再持久化在本机 UI 层） */
      await page.reload({ waitUntil: 'networkidle' });
      await page.waitForFunction(() => window.__pbDebug && window.__pbDebug.renders > 0, null, { timeout: 8000 });
      await page.waitForFunction(() => window.__pbDebug.settings && window.__pbDebug.settings.lowThreshold === 20, null, { timeout: 5000 });
      const persisted = await page.evaluate(() => ({
        threshold: document.getElementById('set-threshold').value,
        reduced: document.getElementById('set-reduced').checked,
        debug: window.__pbDebug.settings.lowThreshold
      }));
      eq(persisted.threshold, '20', '刷新后阈值应回到 collector 的默认 20（mock hello low=20）');
      eq(persisted.debug, 20, '刷新后 debug 阈值应为 collector 值');
      eq(persisted.reduced, true, '刷新后降低动效未恢复');
      await page.evaluate(() => localStorage.removeItem('pb.settings.v1'));
    } finally {
      await page.close();
    }
  });

  /* ============ S1. F-02 不透明度：四档映射 / CSS 变量 / 防抖持久化 / 降级锁定 ============ */
  await test('S1 不透明度 F-02：四档映射、实时 CSS 变量、防抖持久化、acrylic 降级锁定', async () => {
    const page = await openPage('?mock&freeze&effects=css');
    try {
      await waitIntroDone(page);
      await sleep(120);
      const init = await page.evaluate(() => ({
        value: document.getElementById('set-opacity').value,
        out: document.getElementById('set-opacity-out').textContent,
        disabled: document.getElementById('set-opacity').disabled,
        noteHidden: document.getElementById('opacity-note').hidden,
        acrylic: document.documentElement.dataset.acrylic,
        alphas: window.__pbDebug.panelAlphas
      }));
      eq(init.value, '60', '不透明度默认应 60');
      eq(init.out, '60%', '不透明度 output 初始值');
      eq(init.disabled, false, 'acrylic 可用时不透明度滑块应可操作');
      eq(init.noteHidden, true, 'acrylic 可用时不应显示降级文案');
      eq(init.acrylic, 'on', 'mock app-state 应带 acrylic=true');
      eq(init.alphas.map(n => n.toFixed(4)).join(','), '0.6000,0.5200,0.6200', '默认 60 的 panelAlphas');

      /* §7.4.1 四档映射：实时改 --panel-a1/2/3（只改自定义属性，不重排） */
      const buckets = [
        { v: 50, a: ['0.5', '0.4333', '0.5167'] },
        { v: 60, a: ['0.6', '0.52', '0.62'] },
        { v: 80, a: ['0.8', '0.76', '0.81'] },
        { v: 100, a: ['1', '1', '1'] }
      ];
      for (const b of buckets) {
        const got = await page.evaluate(v => {
          const input = document.getElementById('set-opacity');
          input.value = String(v);
          input.dispatchEvent(new Event('input', { bubbles: true }));
          const rootStyle = document.documentElement.style;
          return {
            raw: [rootStyle.getPropertyValue('--panel-a1').trim(),
              rootStyle.getPropertyValue('--panel-a2').trim(),
              rootStyle.getPropertyValue('--panel-a3').trim()],
            out: document.getElementById('set-opacity-out').textContent,
            alphas: window.__pbDebug.panelAlphas
          };
        }, b.v);
        eq(got.raw.join(','), b.a.join(','), '不透明度 ' + b.v + ' 的 --panel-a1/2/3 映射');
        eq(got.out, b.v + '%', '不透明度 ' + b.v + ' 的 output');
        eq(got.alphas.map(n => Number(n.toFixed(4))).join(','), b.a.map(Number).join(','),
          '不透明度 ' + b.v + ' 的 panelAlphas');
      }

      /* 仅 input 不持久化；change 后 300ms 防抖才调 update_settings({patch:{opacity}}) */
      const callsBefore = await page.evaluate(() => window.__pbBridge.calls.filter(c => c.cmd === 'update_settings').length);
      await page.evaluate(() => {
        const input = document.getElementById('set-opacity');
        input.value = '66';
        input.dispatchEvent(new Event('input', { bubbles: true }));
      });
      await sleep(450);
      const callsAfterInput = await page.evaluate(() => window.__pbBridge.calls.filter(c => c.cmd === 'update_settings').length);
      eq(callsAfterInput, callsBefore, '仅 input 不应触发持久化（change 才防抖保存）');

      await page.evaluate(() => {
        const input = document.getElementById('set-opacity');
        input.value = '75';
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      });
      await sleep(500);
      const saved = await page.evaluate(() => {
        const calls = window.__pbBridge.calls.filter(c => c.cmd === 'update_settings');
        const last = calls.length ? calls[calls.length - 1] : null;
        const rootStyle = document.documentElement.style;
        const stored = JSON.parse(localStorage.getItem('pb.settings.v1') || '{}');
        return {
          count: calls.length,
          patch: last ? last.args.patch : null,
          mock: window.__pbBridge.settings.opacity,
          value: document.getElementById('set-opacity').value,
          out: document.getElementById('set-opacity-out').textContent,
          raw: [rootStyle.getPropertyValue('--panel-a1').trim(),
            rootStyle.getPropertyValue('--panel-a2').trim(),
            rootStyle.getPropertyValue('--panel-a3').trim()],
          alphas: window.__pbDebug.panelAlphas,
          storedOpacity: Object.prototype.hasOwnProperty.call(stored, 'opacity')
        };
      });
      ok(saved.count >= 1, 'change 后未调用 update_settings');
      eq(saved.patch.opacity, 75, 'update_settings 未传 opacity=75');
      eq(saved.mock, 75, 'mock 设置事实源未更新 opacity');
      eq(saved.value, '75', '不透明度滑块值被重置');
      eq(saved.out, '75%', '不透明度 output 未更新');
      eq(saved.raw.join(','), '0.75,0.7,0.7625', '75 的三档 alpha 映射');
      eq(saved.alphas.map(n => Number(n.toFixed(4))).join(','), '0.75,0.7,0.7625', '75 的 panelAlphas');
      eq(saved.storedOpacity, false, '不透明度不应写 localStorage（§7.6 唯一持久化在 app.json）');

      /* §7.4.2 降级：acrylic=off → 禁用 + 文案，保留当前值；恢复后解锁 */
      await page.evaluate(() => window.__pbMock.appState({ acrylic: false }));
      await sleep(140);
      const degraded = await page.evaluate(() => ({
        disabled: document.getElementById('set-opacity').disabled,
        noteHidden: document.getElementById('opacity-note').hidden,
        note: document.getElementById('opacity-note').textContent,
        value: document.getElementById('set-opacity').value
      }));
      eq(degraded.disabled, true, 'acrylic=off 时应禁用不透明度滑块');
      eq(degraded.noteHidden, false, 'acrylic=off 时应显示降级文案');
      includes(degraded.note, '当前系统不支持玻璃模糊，已锁定为不透明背景', '降级文案');
      eq(degraded.value, '75', '降级时不应重置当前不透明度值');

      await page.evaluate(() => window.__pbMock.appState({ acrylic: true }));
      await sleep(140);
      const restored = await page.evaluate(() => ({
        disabled: document.getElementById('set-opacity').disabled,
        noteHidden: document.getElementById('opacity-note').hidden
      }));
      eq(restored.disabled, false, 'acrylic 恢复后应解锁不透明度滑块');
      eq(restored.noteHidden, true, 'acrylic 恢复后应隐藏降级文案');

      /* Win10（windows11=false）同样锁定；恢复 Win11 后解锁 */
      await page.evaluate(() => window.__pbMock.appState({ platform: { windows11: false, build: 19045 }, acrylic: true }));
      await sleep(140);
      eq(await page.evaluate(() => document.getElementById('set-opacity').disabled), true, 'Win10 应锁定不透明度滑块');
      await page.evaluate(() => window.__pbMock.appState({ platform: { windows11: true, build: 22631 }, acrylic: true }));
      await sleep(140);
      eq(await page.evaluate(() => document.getElementById('set-opacity').disabled), false, 'Win11 恢复后应解锁');
    } finally {
      await page.close();
    }
  });

  /* ============ S2. S-01 抽屉分组 / 自启三态 / 键盘开关 / 通知 IPC ============ */
  await test('S2 设置抽屉 S-01：分组语义、自启三态、键盘开关、通知持久化 IPC', async () => {
    const page = await openPage('?mock&freeze&effects=css');
    try {
      await waitIntroDone(page);
      await page.evaluate(() => document.getElementById('btn-settings').click());
      await page.waitForSelector('#drawer.is-open', { timeout: 2000 });

      const initial = await page.evaluate(() => ({
        checked: document.getElementById('set-autostart').checked,
        status: document.getElementById('autostart-status').textContent,
        state: document.getElementById('autostart-status').dataset.state,
        noteHidden: document.getElementById('autostart-note').hidden
      }));
      eq(initial.checked, false, '默认自启开关应为关');
      eq(initial.status, '未启用', '默认自启文案');
      eq(initial.state, 'off', '默认自启 data-state');
      eq(initial.noteHidden, true, '系统禁用提示默认隐藏');

      /* 键盘操作：焦点在启动组开关上按 Space → update_settings({patch:{autostart:true}}) */
      await page.evaluate(() => document.getElementById('set-autostart').focus());
      await page.keyboard.press('Space');
      await page.waitForFunction(() => window.__pbBridge.settings.autostart === true, null, { timeout: 2000 });
      await sleep(140);
      const on = await page.evaluate(() => {
        const calls = window.__pbBridge.calls.filter(c => c.cmd === 'update_settings');
        const last = calls.length ? calls[calls.length - 1] : null;
        return {
          checked: document.getElementById('set-autostart').checked,
          status: document.getElementById('autostart-status').textContent,
          state: document.getElementById('autostart-status').dataset.state,
          noteHidden: document.getElementById('autostart-note').hidden,
          patch: last ? last.args.patch : null,
          mock: window.__pbBridge.settings.autostart,
          autostart: window.__pbBridge.autostartStatus
        };
      });
      eq(on.checked, true, '键盘 Space 未打开自启开关');
      eq(on.status, '已启用', '自启开启后文案');
      eq(on.state, 'on', '自启开启后 data-state');
      eq(on.noteHidden, true, '自启开启后不应显示系统禁用提示');
      eq(on.mock, true, 'mock 设置事实源未更新 autostart');
      ok(on.patch && on.patch.autostart === true, 'update_settings 未发送 patch.autostart');
      ok(on.autostart && on.autostart.enabled === true, 'mock 运行时 enabled 未同步');

      /* 被系统启动项管理禁用：intent 仍为 true，但运行时探测 osDisabled → 开关显示为关 + ⚠ 提示，
         不静默改回（§6.1）。这里走 mock 的独立运行时状态注入（镜像 Rust 的注册表探测）。 */
      await page.evaluate(() => window.__pbMock.setAutostartStatus({
        enabled: true, osDisabled: true, path: 'C:\\Apps\\PerimeterBattery.exe'
      }));
      await sleep(140);
      const osDisabled = await page.evaluate(() => ({
        checked: document.getElementById('set-autostart').checked,
        status: document.getElementById('autostart-status').textContent,
        state: document.getElementById('autostart-status').dataset.state,
        noteHidden: document.getElementById('autostart-note').hidden,
        note: document.getElementById('autostart-note').textContent,
        debug: window.__pbDebug.autostart
      }));
      eq(osDisabled.checked, false, '被系统禁用时开关应显示为关');
      includes(osDisabled.status, '被系统启动项管理禁用', '被系统禁用文案');
      eq(osDisabled.state, 'os-disabled', '被系统禁用 data-state');
      eq(osDisabled.noteHidden, false, '被系统禁用时应显示 ⚠ 提示');
      includes(osDisabled.note, '任务管理器', '系统禁用提示应指引任务管理器');
      ok(osDisabled.debug && osDisabled.debug.osDisabled, 'debug 未回填 osDisabled');

      /* osDisabled 时点击开关：必须显式发 update_settings({patch:{autostart:true}})；
         mock 镜像 Rust apply_with(true, true) 清掉 StartupApproved 禁用标记，响应后显示「已启用」。 */
      const beforeEnableCalls = await page.evaluate(() => window.__pbBridge.calls.filter(c => c.cmd === 'update_settings').length);
      await page.evaluate(() => document.getElementById('set-autostart').click());
      await page.waitForFunction(() => {
        const s = document.getElementById('autostart-status');
        return s && s.textContent === '已启用' && s.dataset.state === 'on';
      }, null, { timeout: 4000 });
      const reEnabled = await page.evaluate((before) => {
        const added = window.__pbBridge.calls.filter(c => c.cmd === 'update_settings').slice(before);
        return {
          added: added.length,
          patch: added.length ? added[added.length - 1].args.patch : null,
          checked: document.getElementById('set-autostart').checked,
          status: window.__pbBridge.autostartStatus
        };
      }, beforeEnableCalls);
      ok(reEnabled.added >= 1, 'osDisabled 时点击开关未发 update_settings');
      ok(reEnabled.patch && reEnabled.patch.autostart === true, 'osDisabled 时点击开关必须发送 autostart:true 补丁');
      eq(reEnabled.checked, true, '启用后开关应显示为开');
      ok(reEnabled.status && reEnabled.status.enabled && !reEnabled.status.osDisabled,
        'mock 运行时状态未镜像 Rust 显式开启语义（清 osDisabled）');

      /* Run 被外部删除：intent=true 但运行时 enabled=false → 显示「未启用」；
         点击开关仍必须发 patch（P1-1 修复：不因 intent 相同而跳过 UI 侧显式开启）。 */
      await page.evaluate(() => window.__pbMock.setAutostartStatus({ enabled: false, osDisabled: false, path: null }));
      await sleep(140);
      const external = await page.evaluate(() => ({
        intent: window.__pbBridge.settings.autostart,
        checked: document.getElementById('set-autostart').checked,
        status: document.getElementById('autostart-status').textContent,
        state: document.getElementById('autostart-status').dataset.state
      }));
      eq(external.intent, true, '外部删除场景 intent 应保持 true');
      eq(external.checked, false, '运行时 enabled=false 时开关应显示为关');
      eq(external.status, '未启用', '外部删除 Run 后应显示未启用');
      const beforePatch = await page.evaluate(() => window.__pbBridge.calls.filter(c => c.cmd === 'update_settings').length);
      await page.evaluate(() => document.getElementById('set-autostart').click());
      await page.waitForFunction((before) => {
        return window.__pbBridge.calls.filter(c => c.cmd === 'update_settings').length > before;
      }, beforePatch, { timeout: 4000 });
      const rePatch = await page.evaluate((before) => {
        const added = window.__pbBridge.calls.filter(c => c.cmd === 'update_settings').slice(before);
        return added.length ? added[added.length - 1].args.patch : null;
      }, beforePatch);
      ok(rePatch && rePatch.autostart === true, 'intent 已为 true 时点击开关仍须发送 autostart:true 补丁');

      /* 通知开关：取消低电通知 → update_settings({patch:{notifyLow:false}}) */
      await page.evaluate(() => document.getElementById('set-notify-low').click());
      await sleep(160);
      const notify = await page.evaluate(() => {
        const calls = window.__pbBridge.calls.filter(c => c.cmd === 'update_settings');
        const last = calls.length ? calls[calls.length - 1] : null;
        return {
          patch: last ? last.args.patch : null,
          mock: window.__pbBridge.settings.notifyLow,
          low: document.getElementById('set-notify-low').checked,
          full: document.getElementById('set-notify-full').checked
        };
      });
      eq(notify.mock, false, '通知设置未写入 mock 事实源');
      eq(notify.low, false, '低电通知开关回填失败');
      ok(notify.patch && notify.patch.notifyLow === false, 'update_settings 未发送 notifyLow 补丁');
      eq(notify.full, true, '充满通知默认勾选不应被影响');

      /* 通知/不透明度以 app-state 为事实源：显式 emit 后控件跟随 */
      await page.evaluate(() => window.__pbMock.appState({
        pinned: false, acrylic: true,
        settings: { opacity: 75, autostart: false, notifyLow: true, notifyFull: false },
        autostart: { enabled: false, osDisabled: false },
        platform: { windows11: true, build: 22631 }
      }));
      await sleep(160);
      const echoed = await page.evaluate(() => ({
        low: document.getElementById('set-notify-low').checked,
        full: document.getElementById('set-notify-full').checked,
        opacity: document.getElementById('set-opacity').value
      }));
      eq(echoed.low, true, 'app-state 未回填低电通知开关');
      eq(echoed.full, false, 'app-state 未回填充满通知开关');
      eq(echoed.opacity, '75', 'app-state 未回填不透明度');

      await screenshot(page, 'ui-dev-settings-groups.png');
    } finally {
      await page.close();
    }
  });

  /* ============ S3. F-04 低电阈值统一 / 一次性 legacy 迁移 ============ */
  await test('S3 F-04 低电阈值：collector 唯一来源、乐观更新、ring/告警联动、一次性迁移', async () => {
    const page = await openPage('?mock&freeze&effects=css');
    try {
      await waitIntroDone(page);
      await page.waitForFunction(() => window.__pbDebug.devices === 5, null, { timeout: 5000 });
      const init = await page.evaluate(() => ({
        slider: document.getElementById('set-threshold').value,
        out: document.getElementById('set-threshold-out').textContent,
        debug: window.__pbDebug.settings.lowThreshold,
        collectorLow: window.__pbBridge.collectorLow,
        alarm: !document.getElementById('hero-alert').hidden,
        lowClass: document.getElementById('panel').classList.contains('is-low'),
        ring: document.getElementById('hero-ring').getAttribute('class')
      }));
      eq(init.slider, '20', '阈值初始值应来自 collector hello.settings.low=20');
      eq(init.out, '20%', '阈值 output 初始值');
      eq(init.debug, 20, 'debug 阈值应为 collector 值');
      eq(init.collectorLow, 20, 'mock collectorLow 初始应为 20');
      ok(init.alarm, '默认阈值 20 下 12% 主卡应显示低电告警');
      ok(init.lowClass, '默认阈值下主卡应带 is-low（预烘焙高光层可见）');
      includes(init.ring, 'state-low', '默认阈值下环状态应为低电');

      /* 阈值调低到 5：告警/环状态/高光层全部由 collectorLow 驱动而解除 */
      await page.evaluate(() => {
        const input = document.getElementById('set-threshold');
        input.value = '5';
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      });
      await sleep(140);
      const low5 = await page.evaluate(() => {
        const sent = window.__pbBridge.calls.filter(c => c.cmd === 'collector_send');
        const last = sent.length ? JSON.parse(sent[sent.length - 1].args.line) : null;
        return {
          alarm: !document.getElementById('hero-alert').hidden,
          lowClass: document.getElementById('panel').classList.contains('is-low'),
          ring: document.getElementById('hero-ring').getAttribute('class'),
          sent: last,
          collectorLow: window.__pbBridge.collectorLow,
          debug: window.__pbDebug.settings.lowThreshold
        };
      });
      eq(low5.alarm, false, '阈值 5 时 12% 不应仍判低电');
      eq(low5.lowClass, false, '阈值 5 时主卡 is-low 应移除');
      ok(low5.ring.indexOf('state-low') < 0, '阈值 5 时环不应是低电状态');
      includes(low5.ring, 'state-amber', '阈值 5 时 12% 应降为 amber 档');
      eq(low5.collectorLow, 5, 'collector 未收到 low=5');
      eq(low5.debug, 5, 'UI 阈值未乐观更新');
      ok(low5.sent, 'change 后未发送 collector_send');
      eq(low5.sent.t, 'set', 'collector 消息类型应为 set');
      eq(low5.sent.field, 'low', 'collector set 字段应为 low');
      eq(low5.sent.value, 5, 'collector set 值应为 5');
      eq(Object.prototype.hasOwnProperty.call(low5.sent, 'key'), false, 'collector set 不应带 key');

      /* 收到 hello.settings.low 变化 → 滑块/output/渲染同步（hidden 走 dirty 由 G6 覆盖） */
      await page.evaluate(() => window.__pbMock.hello({ settings: { low: 15 } }));
      await sleep(180);
      const synced = await page.evaluate(() => ({
        slider: document.getElementById('set-threshold').value,
        out: document.getElementById('set-threshold-out').textContent,
        debug: window.__pbDebug.settings.lowThreshold,
        alarm: !document.getElementById('hero-alert').hidden
      }));
      eq(synced.slider, '15', 'hello.settings.low 变化未同步滑块');
      eq(synced.out, '15%', 'hello.settings.low 变化未同步 output');
      eq(synced.debug, 15, 'hello.settings.low 变化未同步 debug');
      ok(synced.alarm, '阈值 15 时 12% 应重新低电告警');

      /* P2-4 边界：协议 0–100。0 是合法阈值（不能在 `Number(...) || 默认值` 里被吞成 20），
         100 也合法；入站 hello.settings.low 越界时只按 0–100 clamp。 */
      await page.evaluate(() => {
        const input = document.getElementById('set-threshold');
        input.value = '0';
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      });
      await page.waitForFunction(() => window.__pbBridge.collectorLow === 0, null, { timeout: 4000 });
      const low0 = await page.evaluate(() => {
        const sent = window.__pbBridge.calls.filter(c => c.cmd === 'collector_send');
        let last = null;
        for (let i = sent.length - 1; i >= 0; i--) {
          try {
            const obj = JSON.parse(sent[i].args.line);
            if (obj && obj.t === 'set' && obj.field === 'low') { last = obj; break; }
          } catch (e) { /* ignore */ }
        }
        return {
          slider: document.getElementById('set-threshold').value,
          out: document.getElementById('set-threshold-out').textContent,
          sent: last,
          debug: window.__pbDebug.settings.lowThreshold,
          alarm: !document.getElementById('hero-alert').hidden,
          lowClass: document.getElementById('panel').classList.contains('is-low')
        };
      });
      eq(low0.slider, '0', '阈值 0 应同步滑块');
      eq(low0.out, '0%', '阈值 0 应同步 output');
      ok(low0.sent, '拖动到 0 后未发送 collector_send');
      eq(low0.sent.value, 0, '拖动到 0 必须发送 low=0（不能退化为默认 20）');
      eq(Object.prototype.hasOwnProperty.call(low0.sent, 'key'), false, '边界 set 仍不应带 key');
      eq(low0.debug, 0, '阈值 0 应乐观更新 debug');
      eq(low0.alarm, false, '阈值 0 时 12% 不应告警');
      eq(low0.lowClass, false, '阈值 0 时主卡不应 is-low');

      /* 100 边界：hello 下发 100 → 12% 必告警（阈值上限） */
      await page.evaluate(() => window.__pbMock.hello({ settings: { low: 100 } }));
      await page.waitForFunction(() => document.getElementById('set-threshold').value === '100', null, { timeout: 4000 });
      const low100 = await page.evaluate(() => ({
        out: document.getElementById('set-threshold-out').textContent,
        debug: window.__pbDebug.settings.lowThreshold,
        alarm: !document.getElementById('hero-alert').hidden,
        lowClass: document.getElementById('panel').classList.contains('is-low')
      }));
      eq(low100.out, '100%', '阈值 100 应同步 output');
      eq(low100.debug, 100, 'hello low=100 应原样保留');
      ok(low100.alarm, '阈值 100 时 12% 应告警');
      ok(low100.lowClass, '阈值 100 时主卡应带 is-low');

      /* 入站越界 clamp：-10 → 0，150 → 100 */
      await page.evaluate(() => window.__pbMock.hello({ settings: { low: -10 } }));
      await page.waitForFunction(() => window.__pbDebug.settings.lowThreshold === 0, null, { timeout: 4000 });
      const clampLow = await page.evaluate(() => ({
        slider: document.getElementById('set-threshold').value,
        out: document.getElementById('set-threshold-out').textContent
      }));
      eq(clampLow.slider, '0', 'hello low=-10 应 clamp 到 0');
      eq(clampLow.out, '0%', 'hello low=-10 clamp 后 output 同步');
      await page.evaluate(() => window.__pbMock.hello({ settings: { low: 150 } }));
      await page.waitForFunction(() => document.getElementById('set-threshold').value === '100', null, { timeout: 4000 });
      const clampHigh = await page.evaluate(() => document.getElementById('set-threshold-out').textContent);
      eq(clampHigh, '100%', 'hello low=150 应 clamp 到 100');
    } finally {
      await page.close();
    }

    /* 一次性迁移：旧 localStorage.lowThreshold=35 且 collector 仍默认 20 → 只推送一次 set low=35，
       随后删除 localStorage 的 lowThreshold（localStorage 只保留 UI 偏好） */
    const legacy = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 2 });
    try {
      await legacy.addInitScript(() => {
        localStorage.setItem('pb.settings.v1', JSON.stringify({ lowThreshold: 35, reducedMotion: false, pinned: false }));
      });
      await legacy.goto(baseUrl + '?mock&freeze&effects=css', { waitUntil: 'networkidle' });
      await legacy.waitForFunction(() => window.__pbDebug && window.__pbDebug.renders > 0, null, { timeout: 8000 });
      await sleep(320);
      const migrated = await legacy.evaluate(() => {
        const lows = [];
        window.__pbBridge.calls.filter(c => c.cmd === 'collector_send').forEach(c => {
          try {
            const obj = JSON.parse(c.args.line);
            if (obj && obj.t === 'set' && obj.field === 'low') lows.push(obj);
          } catch (e) { /* ignore */ }
        });
        const stored = JSON.parse(localStorage.getItem('pb.settings.v1') || '{}');
        return {
          count: lows.length,
          sent: lows.length ? lows[lows.length - 1] : null,
          collectorLow: window.__pbBridge.collectorLow,
          slider: document.getElementById('set-threshold').value,
          debug: window.__pbDebug.settings.lowThreshold,
          storedHasLow: Object.prototype.hasOwnProperty.call(stored, 'lowThreshold'),
          storedReduced: stored.reducedMotion
        };
      });
      eq(migrated.count, 1, '旧 lowThreshold 应只推送一次');
      eq(migrated.sent.value, 35, '迁移推送值应为旧 lowThreshold');
      eq(migrated.collectorLow, 35, '迁移后 collector low 应为 35');
      eq(migrated.slider, '35', '迁移后滑块应显示 35');
      eq(migrated.debug, 35, '迁移后 debug 阈值应为 35');
      eq(migrated.storedHasLow, false, '迁移后 localStorage 应删除 lowThreshold');
      eq(migrated.storedReduced, false, 'localStorage 其它 UI 偏好应保留');
    } finally {
      await legacy.close();
    }
  });

  /* ============ S4. §21.2 动效契约：低电高光层 / 遮罩 / 开关 / 滑块 ============ */
  await test('S4 §21.2 动效契约：250ms 低电高光/无 box-shadow 过渡、遮罩淡入淡出、开关与滑块过渡', async () => {
    const page = await openPage('?mock&freeze&effects=css');
    const step = async (label, fn) => { try { return await fn(); } catch (e) { throw new Error(label + ' :: ' + e.message); } };
    try {
      await waitIntroDone(page);
      await sleep(120);
      const cs = await page.evaluate(() => {
        const panel = getComputedStyle(document.getElementById('panel'));
        const veil = getComputedStyle(document.getElementById('low-veil'));
        const drawer = getComputedStyle(document.getElementById('drawer'));
        const scrim = getComputedStyle(document.getElementById('drawer-scrim'));
        return {
          panelTransition: panel.transitionProperty + '|' + panel.transitionDuration,
          veilPosition: veil.position,
          veilPointer: veil.pointerEvents,
          veilTransition: veil.transitionProperty + '|' + veil.transitionDuration,
          veilWidth: veil.width,
          veilOpacity: veil.opacity,
          drawerTransition: drawer.transitionProperty + '|' + drawer.transitionDuration,
          scrimTransition: scrim.transitionProperty + '|' + scrim.transitionDuration,
          scrimHidden: document.getElementById('drawer-scrim').hidden
        };
      });
      includes(cs.panelTransition, 'border-color', '面板过渡应包含 border-color');
      includes(cs.panelTransition, '0.25s', '面板 border-color 过渡时长应为 250ms');
      ok(cs.panelTransition.indexOf('box-shadow') < 0, 'box-shadow 不得进入 transition（§21.4）');
      eq(cs.veilPosition, 'absolute', '低电高光层应为绝对定位');
      eq(cs.veilPointer, 'none', '低电高光层不应拦截指针事件');
      includes(cs.veilTransition, 'opacity', '低电高光层过渡应为 opacity');
      includes(cs.veilTransition, '0.25s', '低电高光层过渡时长应为 250ms');
      ok(Math.abs(parseFloat(cs.veilWidth) - 718) <= 2, '低电高光层应覆盖整个面板（除 1px 边框内）');
      includes(cs.drawerTransition, 'transform', '抽屉过渡应包含 transform');
      includes(cs.drawerTransition, '0.26s', '抽屉 transform 过渡应为 260ms');
      includes(cs.scrimTransition, 'opacity', '遮罩过渡应为 opacity');
      includes(cs.scrimTransition, '0.2s', '遮罩过渡应为 200ms');
      eq(cs.scrimHidden, true, '遮罩初始应为 hidden');

      /* 遮罩打开：JS 移除 hidden → 下一帧 .is-visible → 200ms 淡入；关闭先移类 200ms 后 hidden */
      await step('open-1', () => page.evaluate(() => document.getElementById('btn-settings').click()));
      await step('scrim-show-1', () => page.waitForFunction(() => !document.getElementById('drawer-scrim').hidden && document.getElementById('drawer-scrim').classList.contains('is-visible'), null, { timeout: 6000 }));
      await step('scrim-fade-1', () => page.waitForFunction(() => parseFloat(getComputedStyle(document.getElementById('drawer-scrim')).opacity) >= 0.999, null, { timeout: 6000 }));
      const scrimOpen = await page.evaluate(() => ({
        hidden: document.getElementById('drawer-scrim').hidden,
        visible: document.getElementById('drawer-scrim').classList.contains('is-visible'),
        opacity: parseFloat(getComputedStyle(document.getElementById('drawer-scrim')).opacity)
      }));
      eq(scrimOpen.hidden, false, '打开抽屉时遮罩不应 hidden');
      eq(scrimOpen.visible, true, '打开抽屉后遮罩应带 is-visible');
      ok(scrimOpen.opacity >= 0.999, '遮罩打开后 opacity 应为 1');

      await step('esc', () => page.keyboard.press('Escape'));
      await step('scrim-hide-class', () => page.waitForFunction(() => !document.getElementById('drawer-scrim').classList.contains('is-visible'), null, { timeout: 6000 }));
      const closing = await page.evaluate(() => ({
        visible: document.getElementById('drawer-scrim').classList.contains('is-visible'),
        hidden: document.getElementById('drawer-scrim').hidden
      }));
      ok(!closing.visible, '关闭抽屉时遮罩应立即移除 is-visible');
      eq(closing.hidden, false, '遮罩淡出 200ms 内不应立刻 hidden');
      await step('scrim-hidden-later', () => page.waitForFunction(() => document.getElementById('drawer-scrim').hidden === true, null, { timeout: 6000 }));

      /* 点击遮罩空白处关闭抽屉（打开后可点空白关闭） */
      await step('open-2', () => page.evaluate(() => document.getElementById('btn-settings').click()));
      await step('scrim-show-2', () => page.waitForFunction(() => !document.getElementById('drawer-scrim').hidden && document.getElementById('drawer-scrim').classList.contains('is-visible'), null, { timeout: 6000 }));
      const scrimBox = await page.evaluate(() => {
        const r = document.getElementById('drawer-scrim').getBoundingClientRect();
        return { x: r.x, y: r.y, w: r.width, h: r.height };
      });
      await step('click-scrim', () => page.mouse.click(scrimBox.x + 30, scrimBox.y + scrimBox.h / 2));
      await step('drawer-closed-by-scrim', () => page.waitForFunction(() => !document.getElementById('drawer').classList.contains('is-open'), null, { timeout: 6000 }));

      /* 静态兜底：开关 160ms / 滑块 thumb 140ms 按下态 / 低电过渡不碰 box-shadow */
      const css = fs.readFileSync(path.join(UI_DIR, 'styles.css'), 'utf8');
      includes(css, 'scale(1.15)', '不透明度滑块 thumb 按下态缺少 scale(1.15)');
      includes(css, '0.14s', '滑块 thumb 过渡应为 140ms');
      includes(css, '0.16s', '开关过渡应为 160ms');
      includes(css, '--panel-a1', 'CSS 缺少面板不透明度变量 --panel-a1');
      const cssNoComments = css.replace(/\/\*[\s\S]*?\*\//g, '');
      ok(!/transition[^;{}]*box-shadow/.test(cssNoComments), 'CSS 中不得有 box-shadow 过渡（§21.4）');
      ok(/backdrop-filter/.test(css), '评审模式 backdrop-filter 规则丢失');
      const appSrc = fs.readFileSync(path.join(UI_DIR, 'app.js'), 'utf8');
      includes(appSrc, 'panelAlphas', 'app.js 缺少 panelAlphas');
      includes(appSrc, 'update_settings', 'app.js 缺少 update_settings 持久化调用');
      includes(appSrc, 'collector_send', 'app.js 缺少 collector_send 阈值同步');
      await screenshot(page, 'ui-dev-motion-contract.png');
    } finally {
      await page.close();
    }
  });

  /* ============ S5. 复审回归：mock 运行时自启状态 / 失败回滚 / mock 版本号 ============ */
  await test('S5 复审回归：update_settings 失败回滚（不透明度/自启）+ warn toast、mock 版本 0.2.0', async () => {
    const page = await openPage('?mock&freeze&effects=css');
    try {
      await waitIntroDone(page);
      /* P3-6：mock 版本号 0.2.0 经 app-state 落到「关于」 */
      await page.waitForFunction(() => {
        const n = document.getElementById('info-version');
        return n && n.textContent.trim() === '0.2.0';
      }, null, { timeout: 4000 });
      await page.evaluate(() => document.getElementById('btn-settings').click());
      await page.waitForSelector('#drawer.is-open', { timeout: 2000 });

      /* P3-9③：不透明度保存失败 → 回滚到上次确认值（60）+ toast，真实等待异步回滚 */
      await page.evaluate(() => window.__pbMock.failUpdateSettings(true));
      await page.evaluate(() => {
        const input = document.getElementById('set-opacity');
        input.value = '75';
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      });
      await page.waitForFunction(() => document.getElementById('set-opacity').value === '60', null, { timeout: 4000 });
      const rollbackOpacity = await page.evaluate(() => ({
        slider: document.getElementById('set-opacity').value,
        out: document.getElementById('set-opacity-out').textContent,
        a1: document.documentElement.style.getPropertyValue('--panel-a1').trim(),
        a2: document.documentElement.style.getPropertyValue('--panel-a2').trim(),
        a3: document.documentElement.style.getPropertyValue('--panel-a3').trim(),
        mock: window.__pbBridge.settings.opacity,
        failedCall: window.__pbBridge.calls.some(c => c.cmd === 'update_settings' && c.args && c.args.patch && c.args.patch.opacity === 75),
        toasts: Array.from(document.querySelectorAll('.toast')).map(n => ({
          cls: n.className,
          text: n.textContent,
          border: getComputedStyle(n).borderTopColor
        }))
      }));
      eq(rollbackOpacity.slider, '60', '失败后不透明度滑块未回滚到上次确认值');
      eq(rollbackOpacity.out, '60%', '失败后不透明度 output 未回滚');
      eq(rollbackOpacity.a1, '0.6', '失败后 --panel-a1 未回滚');
      eq(rollbackOpacity.a2, '0.52', '失败后 --panel-a2 未回滚');
      eq(rollbackOpacity.a3, '0.62', '失败后 --panel-a3 未回滚');
      eq(rollbackOpacity.mock, 60, '失败后 mock 不透明度不应变化');
      ok(rollbackOpacity.failedCall, '未观察到失败的 update_settings 调用（patch.opacity=75）');
      const warnToast = rollbackOpacity.toasts.find(t => t.cls.split(/\s+/).indexOf('is-warn') >= 0);
      ok(warnToast, '不透明度保存失败未出现 warn toast');
      includes(warnToast.text, '不透明度保存失败', 'warn toast 文案');
      includes(warnToast.border, '255, 194, 75', 'warn toast 未应用 amber 边框配色（.toast.is-warn）');

      /* P3-9③：自启保存失败 → 开关回滚原状态（关）+ toast */
      await page.evaluate(() => document.getElementById('set-autostart').click());
      await page.waitForFunction(() => {
        const box = document.getElementById('set-autostart');
        const status = document.getElementById('autostart-status');
        return box && box.checked === false && status && status.dataset.state === 'off';
      }, null, { timeout: 4000 });
      const rollbackAuto = await page.evaluate(() => ({
        checked: document.getElementById('set-autostart').checked,
        status: document.getElementById('autostart-status').textContent,
        mock: window.__pbBridge.settings.autostart,
        toasts: Array.from(document.querySelectorAll('.toast')).map(n => n.textContent)
      }));
      eq(rollbackAuto.checked, false, '自启保存失败后开关未回滚为关');
      eq(rollbackAuto.status, '未启用', '自启保存失败后状态文案未回滚');
      eq(rollbackAuto.mock, false, '自启保存失败后 mock intent 不应变化');
      ok(rollbackAuto.toasts.some(t => t.indexOf('开机启动设置失败') >= 0), '自启保存失败未出现 toast');

      /* 解除失败注入后设置可正常保存（切换回滚路径不影响后续成功路径） */
      await page.evaluate(() => window.__pbMock.failUpdateSettings(false));
      await page.evaluate(() => document.getElementById('set-autostart').click());
      await page.waitForFunction(() => window.__pbBridge.settings.autostart === true, null, { timeout: 4000 });
      await page.waitForFunction(() => document.getElementById('autostart-status').textContent === '已启用', null, { timeout: 4000 });
      await screenshot(page, 'ui-dev-settings-rollback.png');
    } finally {
      await page.close();
    }
  });

  /* ============ S6. prefers-reduced-transparency 降级锁：滑块禁用 + 文案 + 保留当前值 ============ */
  await test('S6 prefers-reduced-transparency 降级：锁定不透明度滑块、提示文案、不重置当前值、恢复后解锁', async () => {
    const page = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 2 });
    try {
      await page.addInitScript(() => {
        const real = window.matchMedia.bind(window);
        let rtMatches = true;
        const rtListeners = [];
        window.matchMedia = function (query) {
          const q = String(query || '');
          if (q.indexOf('prefers-reduced-transparency') >= 0) {
            return {
              get matches() { return rtMatches; },
              media: q,
              onchange: null,
              addEventListener: function (type, fn) {
                if (type === 'change' && typeof fn === 'function') rtListeners.push(fn);
              },
              removeEventListener: function () {},
              addListener: function (fn) { if (typeof fn === 'function') rtListeners.push(fn); },
              removeListener: function () {},
              dispatchEvent: function () { return false; }
            };
          }
          return real(query);
        };
        window.__pbRT = {
          set: function (matches) {
            rtMatches = !!matches;
            rtListeners.forEach(function (fn) { try { fn({ matches: rtMatches }); } catch (e) { /* ignore */ } });
          }
        };
      });
      await page.goto(baseUrl + '?mock&freeze&effects=css', { waitUntil: 'networkidle' });
      await page.waitForFunction(() => window.__pbDebug && window.__pbDebug.renders > 0, null, { timeout: 8000 });
      await waitIntroDone(page);
      await page.evaluate(() => document.getElementById('btn-settings').click());
      await page.waitForSelector('#drawer.is-open', { timeout: 2000 });
      const locked = await page.evaluate(() => ({
        disabled: document.getElementById('set-opacity').disabled,
        value: document.getElementById('set-opacity').value,
        out: document.getElementById('set-opacity-out').textContent,
        noteHidden: document.getElementById('opacity-note').hidden,
        note: document.getElementById('opacity-note').textContent,
        a1: document.documentElement.style.getPropertyValue('--panel-a1').trim(),
        acrylic: document.documentElement.dataset.acrylic,
        reducedMotion: document.documentElement.dataset.reducedMotion || ''
      }));
      eq(locked.disabled, true, '降低透明度命中时滑块应禁用');
      eq(locked.value, '60', '降级锁不应重置当前值');
      eq(locked.out, '60%', '降级锁不应重置 output');
      eq(locked.noteHidden, false, '降级提示应可见');
      includes(locked.note, '当前系统不支持玻璃模糊，已锁定为不透明背景', '降级提示文案');
      eq(locked.a1, '0.6', '降级锁不应重置已应用的 --panel-a1');
      eq(locked.acrylic, 'on', '降级锁不应改写 data-acrylic');
      eq(locked.reducedMotion, '', '仅 reduced-transparency 命中不应触发 reduced-motion 降级');

      /* 系统偏好恢复（matchMedia change 事件）→ 重新求值解锁，值保持不变 */
      await page.evaluate(() => window.__pbRT.set(false));
      await page.waitForFunction(() => !document.getElementById('set-opacity').disabled, null, { timeout: 4000 });
      const unlocked = await page.evaluate(() => ({
        value: document.getElementById('set-opacity').value,
        noteHidden: document.getElementById('opacity-note').hidden,
        a1: document.documentElement.style.getPropertyValue('--panel-a1').trim()
      }));
      eq(unlocked.value, '60', '解锁后当前值不应被重置');
      eq(unlocked.noteHidden, true, '解锁后降级提示应隐藏');
      eq(unlocked.a1, '0.6', '解锁不应改变已应用的面板不透明度');
    } finally {
      await page.close();
    }
  });

  /* ============ 附加 C. 柔光 / 涟漪 / rAF 按需 / Win10 回退 ============ */
  await test('A3 指针柔光与涟漪（PNG+transform）在 CSP 下工作，rAF 按需启动', async () => {
    const page = await openPage('?demo&effects=css');
    try {
      const initial = await page.evaluate(() => ({
        transform: document.getElementById('specular').style.transform,
        rafTicks: window.__pbDebug.rafTicks
      }));

      await page.mouse.move(W * 0.7, H * 0.4);
      await sleep(160);
      const moved = await page.evaluate(() => ({
        transform: document.getElementById('specular').style.transform,
        opacity: getComputedStyle(document.getElementById('specular')).opacity,
        tracking: document.getElementById('glow-layer').classList.contains('is-tracking'),
        willChange: getComputedStyle(document.getElementById('specular')).willChange,
        rafTicks: window.__pbDebug.rafTicks,
        blend: getComputedStyle(document.getElementById('specular')).mixBlendMode
      }));
      eq(moved.opacity, '1', '柔光层不透明度异常（应只动 transform）');
      ok(moved.tracking, '指针进入后未进入 tracking（will-change 生命周期）');
      ok(moved.transform !== initial.transform, '柔光 transform 未随指针更新');
      ok(moved.rafTicks > initial.rafTicks, 'pointermove 没有走 rAF 合并');
      includes(moved.willChange, 'transform', '指针进入期间柔光未加 will-change');
      eq(moved.blend, 'normal', '柔光不应有 mix-blend-mode');

      await sleep(220);
      const idle = await page.evaluate(() => ({
        active: window.__pbDebug.rafActive,
        ticks: window.__pbDebug.rafTicks
      }));
      eq(idle.active, false, '指针停下后 rAF 仍在空转');

      /* 点击空白玻璃：预渲染 PNG 涟漪 + transform */
      const before = await page.$$eval('.ripple', n => n.length);
      await page.mouse.click(120, 190);
      await sleep(120);
      const rippleInfo = await page.evaluate(beforeCount => {
        const nodes = document.querySelectorAll('.ripple');
        const r = nodes[nodes.length - 1];
        return {
          count: nodes.length,
          before: beforeCount,
          exists: !!r,
          bg: r ? getComputedStyle(r).backgroundImage : '',
          blend: r ? getComputedStyle(r).mixBlendMode : '',
          animation: r ? getComputedStyle(r).animationName : ''
        };
      }, before);
      ok(rippleInfo.count > rippleInfo.before, '点击后没有生成涟漪');
      includes(rippleInfo.bg, 'data:image/png', '涟漪不是预渲染 PNG data URI');
      eq(rippleInfo.blend, 'normal', '涟漪仍使用 mix-blend-mode');
      eq(rippleInfo.animation, 'ripple-out', '涟漪动画名异常');

      /* 拖动（pointerdown → move ≥6px → pointerup）不应炸开大涟漪：
         整面板可拖动后，大涟漪只在「原地点击」时补出。 */
      await sleep(800);
      const beforeDrag = await page.$$eval('.ripple', n => n.length);
      await page.mouse.move(120, 190);
      await page.mouse.down();
      await page.mouse.move(180, 240, { steps: 6 });
      await page.mouse.up();
      await sleep(180);
      const dragInfo = await page.evaluate(beforeCount => {
        const nodes = Array.from(document.querySelectorAll('.ripple'));
        return {
          before: beforeCount,
          count: nodes.length,
          large: nodes.filter(n => !n.classList.contains('ripple-sm')).length
        };
      }, beforeDrag);
      eq(dragInfo.count, dragInfo.before, '拖动后面板生成了涟漪（大涟漪应只在点击时触发）：' + JSON.stringify(dragInfo));

      /* 行/按钮的小涟漪保持 pointerdown 立即触发 */
      const rowCenter = await page.evaluate(() => {
        const r = document.querySelector('.row:not([hidden])').getBoundingClientRect();
        return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
      });
      await page.mouse.click(rowCenter.x, rowCenter.y);
      await sleep(80);
      const smallRipple = await page.evaluate(() => ({
        sm: Array.from(document.querySelectorAll('.ripple')).some(n => n.classList.contains('ripple-sm')),
        large: Array.from(document.querySelectorAll('.ripple')).some(n => !n.classList.contains('ripple-sm'))
      }));
      ok(smallRipple.sm, '点击设备行未生成小涟漪（row/button 小涟漪应保持不变）');
      ok(!smallRipple.large, '点击设备行不应生成大涟漪');

      /* Win10 回退：html[data-windows11="false"] 面板给不透明底 */
      await page.evaluate(() => {
        window.__pbMock.appState({
          pinned: false,
          platform: { windows11: false, build: 19045 },
          build: 'dev',
          version: '0.1.0'
        });
      });
      await sleep(80);
      const win10 = await page.evaluate(() => ({
        dataset: document.documentElement.dataset.windows11,
        bg: getComputedStyle(document.getElementById('panel')).backgroundColor
      }));
      eq(win10.dataset, 'false', 'app-state.windows11=false 未落到 html[data-windows11]');
      ok(win10.bg !== 'rgba(0, 0, 0, 0)' && win10.bg !== 'transparent', 'Win10 回退面板不是不透明底：' + win10.bg);
    } finally {
      await page.close();
    }
  });

  /* ============ F1. 隐藏态托盘同步（P1-2） ============ */
  await test('F1 隐藏态 set_tray_state 仍随数据/充电变化同步；首次数据前不发 offline', async () => {
    /* 1) 启动无数据：不应下发 online:false（保持 Rust 的「未知」图标） */
    const emptyPage = await openPage('?mock&freeze&empty');
    try {
      await sleep(150);
      const empty = await emptyPage.evaluate(() => ({
        calls: window.__pbDebug.trayCalls.length,
        devices: window.__pbDebug.devices,
        lastTray: window.__pbDebug.lastTray,
        rows: document.querySelectorAll('.row').length
      }));
      eq(empty.devices, 0, '?mock&empty 应无设备');
      eq(empty.calls, 0, '首次数据到达前不应下发 set_tray_state（避免启动即「离线」图标）');
      eq(empty.lastTray, null, '首次数据到达前不应有托盘 payload');

      /* 首台设备到达 → 立即同步 */
      await emptyPage.evaluate(() => {
        window.__pbMock.device({
          key: 'first:dev:1', name: 'First Device', level: 77, approx: '', left_s: null,
          charging: false, online: true, kind: 'mouse', transport: '2.4g'
        });
      });
      await sleep(120);
      const first = await emptyPage.evaluate(() => ({
        calls: window.__pbDebug.trayCalls.length,
        last: window.__pbDebug.lastTray
      }));
      ok(first.calls >= 1, '首台设备到达后未立即同步托盘');
      eq(first.last.level, 77, '首台设备托盘电量');
      eq(first.last.online, true, '首台设备托盘 online');
      eq(first.last.kind, 'mouse', '首台设备托盘 kind');
    } finally {
      await emptyPage.close();
    }

    /* 2) 隐藏态：level / charging 变化都同步托盘，且不写 DOM / 不动 rAF/timer */
    const page = await openPage('?mock&freeze&hide');
    try {
      await page.waitForFunction(() => window.__pbDebug.hidden === true, null, { timeout: 5000 });
      await sleep(100);
      const before = await page.evaluate(() => ({
        calls: window.__pbDebug.trayCalls.length,
        last: window.__pbDebug.lastTray,
        renders: window.__pbDebug.renders,
        domWrites: window.__pbDebug.domWritesWhileHidden,
        rafTicks: window.__pbDebug.rafTicks,
        timers: window.__pbDebug.timersActive
      }));
      ok(!!before.last, '隐藏前应有已同步的托盘状态');
      eq(before.last.level, 12, '隐藏前托盘应为最低电量 12%');

      /* 最低电量设备 level 变化（隐藏态） */
      await page.evaluate(() => { window.__pbMock.setLevel('logi:046d:c547:unit-12', 8); });
      await sleep(160);
      const afterLevel = await page.evaluate(() => ({
        calls: window.__pbDebug.trayCalls.length,
        last: window.__pbDebug.lastTray
      }));
      ok(afterLevel.calls > before.calls, '隐藏态 level 变化未同步托盘');
      eq(afterLevel.last.level, 8, '隐藏态托盘电量未更新');

      /* charging 变化（隐藏态） */
      await page.evaluate(() => {
        window.__pbMock.device({
          key: 'logi:046d:c547:unit-12', name: 'Arctis Nova 7', level: 8, approx: '',
          left_s: 6480, charging: true, online: true, kind: 'headset', transport: '2.4g'
        });
      });
      await sleep(160);
      const afterCharge = await page.evaluate(() => ({
        calls: window.__pbDebug.trayCalls.length,
        last: window.__pbDebug.lastTray
      }));
      eq(afterCharge.last.charging, true, '隐藏态 charging 变化未同步托盘');
      ok(afterCharge.calls > afterLevel.calls, '隐藏态 charging 变化未触发 set_tray_state');

      const after = await page.evaluate(() => ({
        renders: window.__pbDebug.renders,
        domWrites: window.__pbDebug.domWritesWhileHidden,
        rafTicks: window.__pbDebug.rafTicks,
        timers: window.__pbDebug.timersActive,
        hidden: window.__pbDebug.hidden
      }));
      eq(after.renders, before.renders, '隐藏态托盘同步不应触发 DOM 渲染');
      eq(after.domWrites, 0, '隐藏态托盘同步写入了 DOM');
      eq(after.rafTicks, before.rafTicks, '隐藏态托盘同步动了 rAF');
      eq(after.timers, 0, '隐藏态托盘同步创建了 timer');
      ok(after.hidden, '测试应仍在隐藏态');
    } finally {
      await page.close();
    }
  });

  /* ============ F2. 托盘「设置」/用户动作的 hidden 守卫（P2-1 / P2-3） ============ */
  await test('F2 托盘 settings：可见态直接开抽屉；隐藏态只记状态、显示后打开且不写 DOM', async () => {
    /* 可见态：tray-action settings → 抽屉立即打开 */
    const vis = await openPage('?mock&freeze&effects=css');
    try {
      await vis.evaluate(() => window.__pbMock.trayAction('settings'));
      await vis.waitForFunction(() => document.getElementById('drawer').classList.contains('is-open'), null, { timeout: 2500 });
      const opened = await vis.evaluate(() => ({
        open: document.getElementById('drawer').classList.contains('is-open'),
        expanded: document.getElementById('btn-settings').getAttribute('aria-expanded'),
        focus: document.activeElement && document.activeElement.id
      }));
      ok(opened.open, '可见态托盘 settings 未打开抽屉');
      eq(opened.expanded, 'true', 'aria-expanded 未同步');
      eq(opened.focus, 'set-autostart', '打开抽屉后焦点未进入设置项（S-01 启动组）');
      await screenshot(vis, 'ui-dev-tray-settings.png');
    } finally {
      await vis.close();
    }

    /* 隐藏态：settings/refresh/toggle-pin 只记状态，不写 DOM / 不建 WAAPI / 不建 timer */
    const page = await openPage('?mock&freeze&effects=css&hide');
    try {
      await page.waitForFunction(() => window.__pbDebug.hidden === true, null, { timeout: 5000 });
      await sleep(100);
      const before = await page.evaluate(() => ({
        renders: window.__pbDebug.renders,
        domWrites: window.__pbDebug.domWritesWhileHidden,
        animations: document.getAnimations().length,
        timers: window.__pbDebug.timersActive,
        rafTicks: window.__pbDebug.rafTicks,
        drawerOpen: document.getElementById('drawer').classList.contains('is-open'),
        pinOn: document.getElementById('btn-pin').classList.contains('is-on')
      }));

      await page.evaluate(() => {
        window.__pbMock.trayAction('settings');
        window.__pbMock.trayAction('refresh');
        window.__pbMock.trayAction('toggle-pin');
      });
      await sleep(220);

      const during = await page.evaluate(() => ({
        renders: window.__pbDebug.renders,
        domWrites: window.__pbDebug.domWritesWhileHidden,
        animations: document.getAnimations().length,
        timers: window.__pbDebug.timersActive,
        rafTicks: window.__pbDebug.rafTicks,
        drawerOpen: document.getElementById('drawer').classList.contains('is-open'),
        pendingSettings: window.__pbDebug.pendingSettings,
        pendingSpin: window.__pbDebug.pendingSpin,
        pinOn: document.getElementById('btn-pin').classList.contains('is-on'),
        bridgePinned: window.__pbBridge.pinned,
        blocked: window.__pbDebug.hiddenActionsBlocked
      }));
      eq(during.renders, before.renders, '隐藏态托盘动作触发了 DOM 渲染');
      eq(during.domWrites, 0, '隐藏态托盘动作写入了 DOM（domWritesWhileHidden）');
      eq(during.animations, before.animations, '隐藏态托盘动作创建了 WAAPI（refresh 旋转）');
      eq(during.timers, 0, '隐藏态托盘动作创建了 timer');
      eq(during.rafTicks, before.rafTicks, '隐藏态托盘动作动了 rAF');
      eq(during.drawerOpen, before.drawerOpen, '隐藏态托盘 settings 不应直接改抽屉 DOM');
      ok(!during.drawerOpen, '隐藏态抽屉不应打开');
      eq(during.pendingSettings, true, '隐藏态 settings 意图未记录');
      eq(during.pendingSpin, true, '隐藏态 refresh 意图未记录');
      eq(during.pinOn, before.pinOn, '隐藏态托盘 toggle-pin 不应改按钮 DOM');
      eq(during.bridgePinned, true, '隐藏态 toggle-pin 应通过 set_pinned 更新状态');
      ok(during.blocked >= 3, '隐藏态托盘动作未被 hidden 守卫拦截：' + during.blocked);

      /* 恢复显示：先渲染快照，再补做设置抽屉与刷新动画，pin 回写 */
      await page.evaluate(() => window.__pbMock.visibility(false));
      await page.waitForFunction(() => window.__pbDebug.hidden === false, null, { timeout: 3000 });
      await page.waitForFunction(() => document.getElementById('drawer').classList.contains('is-open'), null, { timeout: 3000 });
      const resumed = await page.evaluate(() => ({
        drawerOpen: document.getElementById('drawer').classList.contains('is-open'),
        pendingSettings: window.__pbDebug.pendingSettings,
        pinOn: document.getElementById('btn-pin').classList.contains('is-on'),
        renders: window.__pbDebug.renders
      }));
      ok(resumed.drawerOpen, '恢复显示后抽屉未打开（tray settings 丢失）');
      eq(resumed.pendingSettings, false, '恢复后 pendingSettings 未清除');
      ok(resumed.pinOn, '恢复显示后 pin 按钮未回写');
      ok(resumed.renders > before.renders, '恢复显示后未渲染快照');
      await screenshot(page, 'ui-dev-tray-resume.png');
    } finally {
      await page.close();
    }
  });

  /* ============ F3. 主卡切换不重建行节点（P3-15） ============ */
  await test('F3 hero 换人不重建行节点、其它行 hover 保持、主卡行用 hidden 切换', async () => {
    const page = await openPage('?mock&freeze&effects=css');
    try {
      await page.waitForFunction(() => window.__pbDebug.rowCount === 4, null, { timeout: 3000 });
      /* 悬停第 3 行（不会被点成主卡），记录全部行节点与主卡行 */
      const third = page.locator('.row:not([hidden])').nth(2);
      const box = await third.boundingBox();
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await sleep(100);

      await page.evaluate(() => {
        window.__f3Nodes = {};
        document.querySelectorAll('.row').forEach(function (node) { window.__f3Nodes[node.dataset.key] = node; });
        window.__f3Keys = Object.keys(window.__f3Nodes);
        window.__f3HeroRow = document.querySelector('.row.is-hero');
        const hoveredNode = document.querySelector('.row:hover');
        window.__f3HoverKey = (hoveredNode || document.querySelector('.row:not([hidden])')).dataset.key;
        window.__f3Creates = window.__pbDebug.rowCreates;
        window.__f3Removes = window.__pbDebug.rowRemoves;
        /* 用键盘把第 2 行设为主卡（不改变鼠标位置） */
        const target = document.querySelectorAll('.row:not([hidden])')[1];
        window.__f3TargetKey = target.dataset.key;
        target.focus();
      });
      await page.keyboard.press('Enter');
      await sleep(160);

      const after = await page.evaluate(() => {
        const nodes = {};
        document.querySelectorAll('.row').forEach(function (node) { nodes[node.dataset.key] = node; });
        const sameNode = window.__f3Keys.every(function (key) { return nodes[key] && nodes[key] === window.__f3Nodes[key]; });
        const hovered = document.querySelector('.row[data-key="' + window.__f3HoverKey + '"]');
        const targetRow = document.querySelector('.row[data-key="' + window.__f3TargetKey + '"]');
        return {
          keys: Object.keys(nodes).sort(),
          sameKeys: Object.keys(nodes).length === window.__f3Keys.length,
          sameNode: sameNode,
          targetKey: window.__f3TargetKey,
          creates: window.__pbDebug.rowCreates,
          removes: window.__pbDebug.rowRemoves,
          createsBefore: window.__f3Creates,
          removesBefore: window.__f3Removes,
          heroKey: window.__pbDebug.heroKey,
          targetHidden: targetRow ? targetRow.hidden : null,
          heroRowVisible: window.__f3HeroRow ? !window.__f3HeroRow.hidden : null,
          hoverKept: hovered ? hovered.matches(':hover') : false,
          hoverSameNode: hovered === window.__f3Nodes[window.__f3HoverKey],
          visibleRows: document.querySelectorAll('.row:not([hidden])').length,
          activeTag: document.activeElement ? document.activeElement.id : ''
        };
      });
      ok(after.sameKeys, 'hero 换人后行节点集合变化');
      ok(after.sameNode, 'hero 换人重建了行 DOM 节点');
      eq(after.creates, after.createsBefore, 'hero 换人创建了新行节点');
      eq(after.removes, after.removesBefore, 'hero 换人删除了行节点');
      eq(after.heroKey, after.targetKey, '主卡未切换到目标设备');
      eq(after.targetHidden, true, '新主卡行未用 hidden 隐藏');
      eq(after.heroRowVisible, true, '旧主卡行未重新显示');
      ok(after.hoverSameNode, '未点中的行节点被替换');
      ok(after.hoverKept, 'hero 换人后其他行 hover 丢失');
      eq(after.visibleRows, 4, 'hero 换人后可见行数异常');
      eq(after.activeTag, 'hero', '键盘设为主卡后焦点未迁移到主卡区域');
      await screenshot(page, 'ui-dev-hero-switch.png');
    } finally {
      await page.close();
    }
  });

  /* ============ F4. 逐条 device 到达：自动主卡=最低电量（runtime-audit F1） ============ */
  await test('F4 逐条 device 到达自动取最低电量；点击固定；固定设备 offline/gone 回落', async () => {
    const page = await openPage('?mock&freeze&empty');
    try {
      const g502 = {
        key: 'logi:046d:c547:g502', name: 'G502 X PLUS', level: 68, approx: '',
        left_s: null, charging: false, online: true, kind: 'mouse', transport: '2.4g'
      };
      const arctis = {
        key: 'ss:1038:2202:arctis', name: 'Arctis Nova 7', level: 12, approx: '',
        left_s: 6480, charging: false, online: true, kind: 'headset', transport: '2.4g'
      };

      /* 1) 先到 G502 68%：只有一台，主卡就是它 */
      await page.evaluate(dev => window.__pbMock.device(dev), g502);
      await sleep(120);
      let s = await page.evaluate(() => ({
        heroKey: window.__pbDebug.heroKey,
        heroName: document.getElementById('hero-name').textContent,
        pinned: window.__pbDebug.heroPinnedByUser
      }));
      eq(s.heroKey, 'logi:046d:c547:g502', '首台设备应成为主卡');
      eq(s.heroName, 'G502 X PLUS', '首台设备主卡名称');
      eq(s.pinned, false, '自动模式不应标记为用户固定');

      /* 2) 后到 Arctis 12%：自动模式必须切到最低电量 */
      await page.evaluate(dev => window.__pbMock.device(dev), arctis);
      await sleep(180);
      s = await page.evaluate(() => ({
        heroKey: window.__pbDebug.heroKey,
        heroName: document.getElementById('hero-name').textContent,
        heroNum: document.getElementById('hero-num').textContent,
        pinned: window.__pbDebug.heroPinnedByUser,
        rows: Array.from(document.querySelectorAll('.row:not([hidden])')).map(n => n.dataset.key)
      }));
      eq(s.heroKey, 'ss:1038:2202:arctis', '后到的更低电量设备未自动成为主卡');
      eq(s.heroName, 'Arctis Nova 7', '自动主卡名称应为 Arctis');
      await page.waitForFunction(() => document.getElementById('hero-num').textContent === '12', null, { timeout: 2000 });
      s = await page.evaluate(() => ({
        pinned: window.__pbDebug.heroPinnedByUser,
        rows: Array.from(document.querySelectorAll('.row:not([hidden])')).map(n => n.dataset.key),
        heroNum: document.getElementById('hero-num').textContent
      }));
      eq(s.heroNum, '12', '自动主卡电量应为 12');
      eq(s.pinned, false, '自动切换不应变成用户固定');
      ok(s.rows.indexOf('logi:046d:c547:g502') >= 0, '旧主卡 G502 未回到列表');
      await screenshot(page, 'ui-dev-hero-auto.png');

      /* 3) 点击 G502 行：用户固定主卡，即使电量更高也不再自动切走 */
      await page.locator('.row:not([hidden])[data-key="logi:046d:c547:g502"]').click();
      await sleep(140);
      await page.waitForFunction(() => document.getElementById('hero-num').textContent === '68', null, { timeout: 2000 });
      s = await page.evaluate(() => ({
        heroKey: window.__pbDebug.heroKey,
        pinned: window.__pbDebug.heroPinnedByUser,
        pinnedKey: window.__pbDebug.pinnedHeroKey
      }));
      eq(s.heroKey, 'logi:046d:c547:g502', '点击行后未固定为主卡');
      eq(s.pinned, true, '点击行后未标记用户固定');
      eq(s.pinnedKey, 'logi:046d:c547:g502', 'pinnedHeroKey 未记录');

      await page.evaluate(() => window.__pbMock.setLevel('ss:1038:2202:arctis', 10));
      await sleep(160);
      s = await page.evaluate(() => ({
        heroKey: window.__pbDebug.heroKey,
        heroNum: document.getElementById('hero-num').textContent
      }));
      eq(s.heroKey, 'logi:046d:c547:g502', '用户固定的主卡被自动切换走了');
      eq(s.heroNum, '68', '固定主卡电量显示异常');

      /* 4) 固定设备 offline → 回落到自动（最低电量 Arctis 10%） */
      await page.evaluate(dev => window.__pbMock.device(Object.assign({}, dev, { online: false })), g502);
      await sleep(160);
      await page.waitForFunction(() => document.getElementById('hero-num').textContent === '10', null, { timeout: 2000 });
      s = await page.evaluate(() => ({
        heroKey: window.__pbDebug.heroKey,
        pinned: window.__pbDebug.heroPinnedByUser,
        heroNum: document.getElementById('hero-num').textContent
      }));
      eq(s.heroKey, 'ss:1038:2202:arctis', '固定设备 offline 后未回落到最低电量');
      eq(s.pinned, false, '固定设备 offline 后未解除固定');
      eq(s.heroNum, '10', '回落后的主卡电量应为 10');

      /* 5) 重新固定 G502 后 gone → 回落自动 */
      await page.evaluate(dev => window.__pbMock.device(Object.assign({}, dev, { online: true })), g502);
      await sleep(120);
      await page.locator('.row:not([hidden])[data-key="logi:046d:c547:g502"]').click();
      await sleep(120);
      s = await page.evaluate(() => ({ heroKey: window.__pbDebug.heroKey, pinned: window.__pbDebug.heroPinnedByUser }));
      eq(s.heroKey, 'logi:046d:c547:g502', '重新固定 G502 失败');
      eq(s.pinned, true, '重新固定后 pinned 标记异常');

      await page.evaluate(() => window.__pbMock.gone('logi:046d:c547:g502'));
      await sleep(160);
      await page.waitForFunction(() => document.getElementById('hero-num').textContent === '10', null, { timeout: 2000 });
      s = await page.evaluate(() => ({
        heroKey: window.__pbDebug.heroKey,
        pinned: window.__pbDebug.heroPinnedByUser,
        heroNum: document.getElementById('hero-num').textContent
      }));
      eq(s.heroKey, 'ss:1038:2202:arctis', '固定设备 gone 后未回落到最低电量');
      eq(s.pinned, false, '固定设备 gone 后 pinned 标记未清除');
      eq(s.heroNum, '10', 'gone 回落后的主卡电量应为 10');
    } finally {
      await page.close();
    }
  });

  /* ============ F5. 低电 toast 不遮挡列表数值（runtime-audit F5） ============ */
  await test('F5 低电 toast 不遮挡列表行右侧电量，面板外框保持 720×380', async () => {
    const page = await openPage('?mock&freeze&effects=css');
    try {
      await page.evaluate(() => {
        window.__pbMock.alert('low', 'logi:046d:c547:unit-12', 12, 'Arctis Nova 7 电量低，建议充电');
      });
      await page.waitForSelector('.toast', { state: 'visible', timeout: 3000 });
      await sleep(120);

      const info = await page.evaluate(() => {
        const toast = document.querySelector('.toast');
        const tr = toast.getBoundingClientRect();
        const levels = Array.from(document.querySelectorAll('.row:not([hidden]) .row-level'))
          .map(node => {
            const r = node.getBoundingClientRect();
            return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, key: node.closest('.row').dataset.key };
          });
        const overlaps = levels.filter(r => !(tr.right < r.left || tr.left > r.right || tr.bottom < r.top || tr.top > r.bottom));
        const panel = document.getElementById('panel').getBoundingClientRect();
        const toastInPanel = tr.left >= panel.left && tr.right <= panel.right && tr.top >= panel.top && tr.bottom <= panel.bottom;
        return {
          overlapKeys: overlaps.map(o => o.key),
          levelCount: levels.length,
          toastInPanel: toastInPanel,
          panel: { x: panel.x, y: panel.y, w: panel.width, h: panel.height }
        };
      });
      eq(info.levelCount, 4, '可见行数异常（mock 应为 4 行）');
      eq(info.overlapKeys.length, 0, 'toast 遮挡了列表行右侧电量数值：' + JSON.stringify(info.overlapKeys));
      ok(info.toastInPanel, 'toast 越出了面板边界');
      eq(Math.round(info.panel.w), 720, 'toast 改变了面板宽度');
      eq(Math.round(info.panel.h), 380, 'toast 改变了面板高度');
      eq(Math.round(info.panel.x), (W - 720) / 2, '面板在预览视口中的位置被改变');
      eq(Math.round(info.panel.y), (H - 380) / 2, '面板在预览视口中的位置被改变');
      await screenshot(page, 'ui-dev-toast.png');
    } finally {
      await page.close();
    }
  });

  /* ============ A4. 整面板拖拽区（P1-08 回归） ============ */
  await test('A4 drag-region="deep"：整个面板可拖（hero/hero-name/footer），行与按钮仍可交互', async () => {
    const html = fs.readFileSync(path.join(UI_DIR, 'index.html'), 'utf8');
    /* 去掉 HTML 注释再匹配属性（注释里解释了拖拽区，不能算属性出现） */
    const markup = html.replace(/<!--[\s\S]*?-->/g, '');
    const dragTags = markup.match(/<[^>]*data-tauri-drag-region[^>]*>/g) || [];
    /* 只允许一处 deep（#panel）；抽屉与 scrim 用 "false" 显式退出整面板拖拽 */
    const deepTags = dragTags.filter(t => t.includes('data-tauri-drag-region="deep"'));
    eq(deepTags.length, 1, 'data-tauri-drag-region="deep" 应只出现一处（#panel）');
    includes(deepTags[0], 'class="panel"', '拖拽区不在 #panel 上');
    includes(deepTags[0], 'id="panel"', '拖拽区不在 #panel 上');
    includes(deepTags[0], 'data-tauri-drag-region="deep"', '拖拽区不是 deep（裸属性只对元素自身生效）');
    const scrimTag = dragTags.find(t => t.includes('id="drawer-scrim"')) || '';
    const drawerTag = dragTags.find(t => t.includes('id="drawer"')) || '';
    includes(scrimTag, 'data-tauri-drag-region="false"',
      'drawer-scrim 未用 "false" 退出拖拽（否则点击关抽屉会被原生拖动吞掉）');
    includes(drawerTag, 'data-tauri-drag-region="false"',
      '抽屉未用 "false" 退出拖拽（设置面板空白处不应拖动窗口）');
    ok(!markup.includes('class="side-head" data-tauri-drag-region'),
      'side-head 仍保留旧拖拽属性（会造成重复/局部拖拽区）');

    const page = await openPage('?mock&freeze&effects=css');
    try {
      const info = await page.evaluate(() => {
        const ATTR = 'data-tauri-drag-region';
        const CLICKABLE_TAGS = ['A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'LABEL', 'SUMMARY'];
        const INTERACTIVE_ROLES = ['button', 'link', 'menuitem', 'tab', 'checkbox', 'radio', 'switch', 'option'];
        const classOf = el => String((el && el.className && el.className.baseVal !== undefined)
          ? el.className.baseVal : (el && el.className) || '');
        const isClickable = (el) => {
          if (CLICKABLE_TAGS.indexOf(String(el.tagName || '').toUpperCase()) >= 0) return true;
          const role = el.getAttribute && el.getAttribute('role');
          if (role && INTERACTIVE_ROLES.indexOf(role) >= 0) return true;
          if (el.isContentEditable) return true;
          const tab = el.getAttribute && el.getAttribute('tabindex');
          return tab !== null && tab !== '-1';
        };
        /* 复刻 Tauri 2.12 drag.js 的 isDragRegion：沿 target 祖先链向上，
           可交互元素会阻断拖拽；deep 命中即整棵子树可拖。 */
        const regionAt = (x, y) => {
          const node = document.elementFromPoint(x, y);
          if (!node) return { hit: '', drag: false, why: 'no-node', region: '' };
          const hit = String(node.tagName || '') + '.' + classOf(node);
          let el = node;
          while (el && el !== document.documentElement) {
            const attr = el.getAttribute ? el.getAttribute(ATTR) : null;
            if (isClickable(el) && attr === null) {
              return { hit: hit, drag: false, why: 'clickable:' + String(el.tagName) + '.' + classOf(el), region: '' };
            }
            if (attr === null) { el = el.parentElement; continue; }
            if (attr === 'false') return { hit: hit, drag: false, why: 'false-region', region: '' };
            if (attr === 'deep') return { hit: hit, drag: true, why: 'deep', region: el.id || classOf(el) };
            if (attr === '' || attr === 'true') {
              return { hit: hit, drag: el === node, why: 'self', region: el.id || classOf(el) };
            }
            el = el.parentElement;
          }
          return { hit: hit, drag: false, why: 'no-region', region: '' };
        };
        const center = (sel) => {
          const r = document.querySelector(sel).getBoundingClientRect();
          return [r.left + r.width / 2, r.top + r.height / 2];
        };
        const headRect = document.querySelector('.side-head').getBoundingClientRect();
        const row = document.querySelector('.row:not([hidden])');
        const rowRect = row.getBoundingClientRect();
        const firstRow = Array.from(document.querySelectorAll('.row:not([hidden])'))[1];
        const fr = firstRow.getBoundingClientRect();
        return {
          panelAttr: document.getElementById('panel').getAttribute(ATTR),
          headAttr: document.querySelector('.side-head').getAttribute(ATTR),
          hero: regionAt.apply(null, center('.hero-card')),
          heroName: regionAt.apply(null, center('.hero-name')),
          footerHint: regionAt.apply(null, center('.side-foot .hint')),
          headerBlank: regionAt(headRect.left + 4, headRect.bottom - 4),
          row: regionAt(rowRect.left + rowRect.width / 2, rowRect.top + rowRect.height / 2),
          button: regionAt.apply(null, center('#btn-settings')),
          firstRowKey: firstRow.dataset.key,
          firstRowName: firstRow.querySelector('.row-name').textContent
        };
      });
      eq(info.panelAttr, 'deep', '#panel 的 drag-region 不是 deep');
      eq(info.headAttr, null, '.side-head 不应再有 drag-region 属性');
      ok(info.hero.drag, 'hero 大卡空白处不可拖：' + JSON.stringify(info.hero));
      ok(info.heroName.drag, 'hero 名称处不可拖：' + JSON.stringify(info.heroName));
      ok(info.footerHint.drag, 'footer 提示处不可拖：' + JSON.stringify(info.footerHint));
      ok(info.headerBlank.drag, 'header 空白处不可拖：' + JSON.stringify(info.headerBlank));
      ok(!info.row.drag, '设备行不应是拖拽区（否则无法点击选主卡）：' + JSON.stringify(info.row));
      includes(info.row.why, 'clickable', '设备行未按可交互元素被排除');
      ok(!info.button.drag, '控制按钮不应是拖拽区：' + JSON.stringify(info.button));
      includes(info.button.why, 'clickable', '控制按钮未按可交互元素被排除');

      /* 行仍能点击选主卡（不被拖拽区吞掉） */
      const rowCenter = await page.evaluate((key) => {
        const r = document.querySelector('.row[data-key="' + key + '"]').getBoundingClientRect();
        return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
      }, info.firstRowKey);
      await page.mouse.click(rowCenter.x, rowCenter.y);
      await sleep(120);
      const afterRow = await page.evaluate(() => ({
        hero: document.getElementById('hero-name').textContent
      }));
      eq(afterRow.hero, info.firstRowName, '点击设备行未切换主卡片（行点击被拖拽区影响）');

      /* 按钮仍能点击：设置按钮打开抽屉 */
      const btnCenter = await page.evaluate(() => {
        const r = document.getElementById('btn-settings').getBoundingClientRect();
        return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
      });
      await page.mouse.click(btnCenter.x, btnCenter.y);
      await sleep(150);
      const drawer = await page.evaluate(() => ({
        expanded: document.getElementById('btn-settings').getAttribute('aria-expanded'),
        open: document.getElementById('drawer').classList.contains('is-open')
      }));
      eq(drawer.expanded, 'true', '设置按钮点击未生效（aria-expanded 未更新）');
      ok(drawer.open, '设置按钮点击未打开抽屉');

      /* scrim 显式退出拖拽（"false"）：点击空白遮罩必须能关抽屉，
         否则整面板 deep 拖拽会把 mousedown 交给原生 start_dragging，点击被吞。 */
      const scrimPoint = await page.evaluate(() => {
        const r = document.getElementById('panel').getBoundingClientRect();
        return { x: r.left + 30, y: r.top + 30 };
      });
      const scrimHit = await page.evaluate(({ x, y }) => {
        const node = document.elementFromPoint(x, y);
        const region = node && node.closest ? node.closest('[data-tauri-drag-region]') : null;
        return {
          id: node ? String(node.id || node.className || node.tagName) : '',
          region: region ? (region.id || String(region.className)) : '',
          attr: region ? region.getAttribute('data-tauri-drag-region') : null
        };
      }, scrimPoint);
      eq(scrimHit.id, 'drawer-scrim', '抽屉打开时面板左上空白应为 scrim：' + JSON.stringify(scrimHit));
      eq(scrimHit.attr, 'false', 'scrim 未以 "false" 退出拖拽区：' + JSON.stringify(scrimHit));
      await page.mouse.click(scrimPoint.x, scrimPoint.y);
      await sleep(180);
      const closedByScrim = await page.evaluate(() =>
        document.getElementById('drawer').classList.contains('is-open'));
      ok(!closedByScrim, '点击 scrim 未关闭抽屉（点击被拖拽区吞掉）');
    } finally {
      await page.close();
    }
  });

  /* ============ A10. left_s_src 渲染（spec ≈ / mains 外接供电 / 旧值不变） ============ */
  await test('A10 left_s_src：spec→≈ 估算、mains→外接供电、history/缺省保持「约」，无值仍「剩余时间未知」', async () => {
    const page = await openPage('?mock&freeze&effects=css');
    try {
      const heroKey = 'logi:046d:c547:unit-12';   /* mock 默认最低电量（12%）→ 主卡片 */
      const rowKey = 'razer:1532:0099:unit-3';
      const push = async (device) => {
        await page.evaluate(d => { window.__pbMock.device(d); }, device);
        await sleep(120);
      };
      const hero = () => page.evaluate(() => {
        const c = document.getElementById('hero-caption');
        return { text: c.textContent, title: c.getAttribute('title') || '' };
      });
      const row = () => page.evaluate(key => {
        const el = document.querySelector('.row[data-key="' + key + '"] .row-left');
        return { text: el.textContent, title: el.getAttribute('title') || '' };
      }, rowKey);

      /* 1) spec 主卡片：≈ + 估算提示 */
      await push({ key: heroKey, name: 'Arctis Nova 7', kind: 'headset', transport: '2.4g',
        level: 12, left_s: 6480, left_s_src: 'spec' });
      const specHero = await hero();
      eq(specHero.text, '≈ 1 小时 48 分', 'spec 主卡片剩余时间文案异常：' + specHero.text);
      includes(specHero.title, '按标称续航估算', 'spec 主卡片缺少估算提示 title');

      /* 2) spec 列表行：紧凑 ≈，且与 humanLeft 的「约」不混用 */
      await push({ key: rowKey, name: 'Viper V3 Pro', kind: 'mouse', transport: '2.4g',
        level: 68, left_s: 7500, left_s_src: 'spec' });
      const specRow = await row();
      eq(specRow.text, '≈ 2 小时 5 分', 'spec 行剩余时间文案异常：' + specRow.text);
      includes(specRow.title, '按标称续航估算', 'spec 行缺少估算提示 title');

      /* 3) mains：主卡片与列表行都显示「外接供电」（left_s 仍保留也不再展示时长） */
      await push({ key: heroKey, name: 'Arctis Nova 7', kind: 'headset', transport: '2.4g',
        level: 12, left_s: 6480, left_s_src: 'mains' });
      eq((await hero()).text, '外接供电', 'mains 主卡片未显示「外接供电」');
      await push({ key: rowKey, name: 'Viper V3 Pro', kind: 'mouse', transport: '2.4g',
        level: 68, left_s: 7500, left_s_src: 'mains' });
      eq((await row()).text, '外接供电', 'mains 行未显示「外接供电」');

      /* 4) history：与旧版「约」文案一致，无估算 title */
      await push({ key: heroKey, name: 'Arctis Nova 7', kind: 'headset', transport: '2.4g',
        level: 12, left_s: 6480, left_s_src: 'history' });
      const hist = await hero();
      eq(hist.text, '约 1 小时 48 分', 'history 主卡片文案被改变：' + hist.text);
      eq(hist.title, '', 'history 不应带估算提示 title');

      /* 5) 旧 collector（无 left_s_src 字段）：保持原样「约」 */
      await push({ key: heroKey, name: 'Arctis Nova 7', kind: 'headset', transport: '2.4g',
        level: 12, left_s: 6480 });
      eq((await hero()).text, '约 1 小时 48 分', '缺省 left_s_src 时文案被改变');

      /* 6) spec 但无估算值：仍「剩余时间未知」，且不出现 ≈ */
      await push({ key: rowKey, name: 'Viper V3 Pro', kind: 'mouse', transport: '2.4g',
        level: 68, left_s: null, left_s_src: 'spec' });
      const noEst = await row();
      eq(noEst.text, '剩余时间未知', 'spec 无估算值时文案异常：' + noEst.text);
      ok(noEst.text.indexOf('≈') < 0, 'spec 无估算值却出现 ≈：' + noEst.text);
      eq(noEst.title, '', 'spec 无估算值不应带估算提示 title');
    } finally {
      await page.close();
    }
  });

  /* ============ A5. 右键菜单拦截（P1-09） ============ */
  await test('A5 contextmenu 被 document 级拦截（生产+浏览器 mock），不弹 WebView2 默认菜单', async () => {
    const page = await openPage('?mock&freeze&effects=css');
    try {
      /* 合成事件：document 级 handler 调用 preventDefault 后事件对象必须被取消 */
      const synth = await page.evaluate(() => {
        const before = window.__pbDebug.blockedContextMenus;
        const ev = new MouseEvent('contextmenu', { bubbles: true, cancelable: true });
        const dispatched = document.querySelector('.hero-card').dispatchEvent(ev);
        return {
          before: before,
          after: window.__pbDebug.blockedContextMenus,
          prevented: ev.defaultPrevented,
          dispatched: dispatched
        };
      });
      eq(synth.prevented, true, '合成 contextmenu 未被 preventDefault');
      eq(synth.dispatched, false, '合成 contextmenu 的 dispatchEvent 未返回 false');
      eq(synth.after, synth.before + 1, 'contextmenu 拦截计数未递增');

      /* 真实右击：走与生产一致的输入事件路径 */
      await page.mouse.click(W * 0.5, H * 0.55, { button: 'right' });
      await sleep(150);
      const real = await page.evaluate(() => ({
        blocked: window.__pbDebug.blockedContextMenus,
        rows: document.querySelectorAll('.row:not([hidden])').length,
        renders: window.__pbDebug.renders
      }));
      ok(real.blocked > synth.after, '真实右击未触发 contextmenu 拦截：' + real.blocked);
      eq(real.rows, 4, '右击后页面状态异常（行数变化）');
      ok(real.renders > 0, '右击后页面被重载');
    } finally {
      await page.close();
    }
  });

  /* ============ A6. 浏览器加速键拦截（P1-09） ============ */
  await test('A6 F5/Ctrl+R/Ctrl+Shift+R/Ctrl+P/Ctrl+F/Ctrl+S/Ctrl+U 被拦截且页面不重载', async () => {
    const page = await openPage('?mock&freeze&effects=css');
    try {
      await waitIntroDone(page);
      const cases = [
        { label: 'F5', key: 'F5' },
        { label: 'Ctrl+R', key: 'r', ctrl: true },
        { label: 'Ctrl+Shift+R', key: 'R', ctrl: true, shift: true },
        { label: 'Ctrl+P', key: 'p', ctrl: true },
        { label: 'Ctrl+F', key: 'f', ctrl: true },
        { label: 'Ctrl+S', key: 's', ctrl: true },
        { label: 'Ctrl+U', key: 'u', ctrl: true }
      ];

      const base = await page.evaluate(() => {
        window.__accelToken = 'alive-' + Date.now();
        return {
          token: window.__accelToken,
          renders: window.__pbDebug.renders,
          blocked: window.__pbDebug.blockedShortcuts
        };
      });

      /* 1) 合成 keydown：defaultPrevented + dispatchEvent 返回 false */
      for (const c of cases) {
        const r = await page.evaluate(c => {
          const ev = new KeyboardEvent('keydown', {
            key: c.key, ctrlKey: !!c.ctrl, shiftKey: !!c.shift, bubbles: true, cancelable: true
          });
          const dispatched = document.getElementById('panel').dispatchEvent(ev);
          return { prevented: ev.defaultPrevented, dispatched: dispatched };
        }, c);
        ok(r.prevented, c.label + ' 未被 preventDefault（合成 keydown）');
        eq(r.dispatched, false, c.label + ' 的 dispatchEvent 未被取消（合成 keydown）');
      }

      /* 2) 真实按键：同样被拦截，且页面不重载（token/渲染计数保持） */
      for (const c of cases) {
        const combo = c.ctrl
          ? (c.shift ? 'Control+Shift+' : 'Control+') + c.key.toUpperCase()
          : c.key;
        await page.keyboard.press(combo);
      }
      await sleep(200);
      const after = await page.evaluate(() => ({
        token: window.__accelToken || '',
        renders: window.__pbDebug.renders,
        blocked: window.__pbDebug.blockedShortcuts
      }));
      eq(after.token, base.token, '页面被加速键重载（token 丢失）');
      eq(after.renders, base.renders, '加速键触发了整页重载/重新渲染');
      ok(after.blocked >= base.blocked + cases.length * 2,
        '加速键拦截计数不足：' + after.blocked + '（合成+真实应至少 +' + (cases.length * 2) + '）');

      /* 3) 文本编辑快捷键不能被误伤 */
      const textKeys = await page.evaluate(() => {
        const out = {};
        ['c', 'v', 'x', 'a', 'z', 'y'].forEach(key => {
          const ev = new KeyboardEvent('keydown', { key: key, ctrlKey: true, bubbles: true, cancelable: true });
          document.getElementById('panel').dispatchEvent(ev);
          out[key] = ev.defaultPrevented;
        });
        return out;
      });
      Object.keys(textKeys).forEach(k => {
        ok(!textKeys[k], 'Ctrl+' + k.toUpperCase() + ' 文本编辑快捷键被误拦截');
      });

      /* 输入框内同样保留编辑快捷键（焦点在抽屉滑块上时 Ctrl+A 不被拦截） */
      const inInput = await page.evaluate(() => {
        document.getElementById('btn-settings').click();
        const input = document.getElementById('set-threshold');
        const ev = new KeyboardEvent('keydown', { key: 'a', ctrlKey: true, bubbles: true, cancelable: true });
        input.dispatchEvent(ev);
        return {
          prevented: ev.defaultPrevented,
          drawerOpen: document.getElementById('drawer').classList.contains('is-open')
        };
      });
      ok(inInput.drawerOpen, '设置抽屉未打开（前置条件失败）');
      ok(!inInput.prevented, '输入框内 Ctrl+A 被误拦截');
    } finally {
      await page.close();
    }
  });

  /* ============ A7. 托盘 pin 契约 + acrylic 降级（A12/A14） ============ */
  await test('A7 app-state 是 pin 事实源（不回调 set_pinned）；acrylic=false 走实底降级', async () => {
    const appJs = fs.readFileSync(path.join(UI_DIR, 'app.js'), 'utf8');
    ok(/action\s*===\s*['"]toggle-pin['"][\s\S]{0,360}bridge\.hasTauri/.test(appJs),
      'toggle-pin 分支缺少 bridge.hasTauri 判断（Tauri 下会与 Rust set_pinned 互相抵消）');

    const page = await openPage('?mock&freeze');
    try {
      /* 1) 浏览器 mock 路径：托盘 toggle-pin 仍本地翻转（现有隐藏态守卫依赖它） */
      const tray = await page.evaluate(() => {
        const callsBefore = window.__pbBridge.calls.filter(c => c.cmd === 'set_pinned').length;
        window.__pbMock.trayAction('toggle-pin');
        return {
          callsBefore: callsBefore,
          callsAfter: window.__pbBridge.calls.filter(c => c.cmd === 'set_pinned').length,
          pinOn: document.getElementById('btn-pin').classList.contains('is-on')
        };
      });
      ok(tray.pinOn, 'mock 路径托盘 toggle-pin 未本地翻转');
      ok(tray.callsAfter > tray.callsBefore, 'mock 路径托盘 toggle-pin 未调用 set_pinned');

      /* 2) app-state 下发 pinned：只同步状态/UI，不得再回调 set_pinned（避免反馈环） */
      const sync = await page.evaluate(() => {
        const callsBefore = window.__pbBridge.calls.filter(c => c.cmd === 'set_pinned').length;
        window.__pbMock.appState({
          pinned: true, platform: { windows11: true, build: 22631 },
          build: 'mock', version: '0.1.0', acrylic: true
        });
        return {
          callsBefore: callsBefore,
          callsAfter: window.__pbBridge.calls.filter(c => c.cmd === 'set_pinned').length,
          pinOn: document.getElementById('btn-pin').classList.contains('is-on'),
          ariaPressed: document.getElementById('btn-pin').getAttribute('aria-pressed'),
          drawerChecked: document.getElementById('set-pinned').checked,
          stored: JSON.parse(localStorage.getItem('pb.settings.v1') || '{}').pinned,
          acrylic: document.documentElement.dataset.acrylic
        };
      });
      eq(sync.callsAfter, sync.callsBefore, 'handleAppState 收到 pinned 后又 invoke set_pinned（反馈环）');
      ok(sync.pinOn, 'app-state pinned=true 未回写 pin 按钮');
      eq(sync.ariaPressed, 'true', 'app-state pinned 未更新 aria-pressed');
      eq(sync.drawerChecked, true, 'app-state pinned 未更新设置抽屉勾选');
      eq(sync.stored, true, 'app-state pinned 未持久化到 localStorage');
      eq(sync.acrylic, 'on', 'app-state acrylic=true 未落到 html[data-acrylic="on"]');

      /* 3) acrylic=false：实底/边框/内阴影降级，且不新增 backdrop-filter */
      const fallback = await page.evaluate(() => {
        const parseAlpha = color => {
          const m = String(color).match(/rgba?\(([^)]+)\)/);
          if (!m) return 1;
          const parts = m[1].split(',').map(Number);
          return parts.length === 4 ? parts[3] : 1;
        };
        window.__pbMock.appState({
          pinned: false, platform: { windows11: true, build: 22631 },
          build: 'mock', version: '0.1.0', acrylic: false
        });
        const cs = getComputedStyle(document.getElementById('panel'));
        return {
          acrylic: document.documentElement.dataset.acrylic,
          bg: cs.backgroundColor,
          bgAlpha: parseAlpha(cs.backgroundColor),
          solid: getComputedStyle(document.documentElement).getPropertyValue('--panel-solid').trim(),
          borderAlpha: parseAlpha(cs.borderTopColor),
          shadow: cs.boxShadow,
          backdrop: cs.backdropFilter,
          webkitBackdrop: (cs.getPropertyValue('-webkit-backdrop-filter') || '').trim() || 'none'
        };
      });
      eq(fallback.acrylic, 'off', 'app-state acrylic=false 未落到 html[data-acrylic="off"]');
      eq(fallback.solid, '#0a1830', '缺少 --panel-solid 实底变量');
      eq(fallback.bgAlpha, 1, 'acrylic=false 时面板背景仍半透明：' + fallback.bg);
      eq(fallback.backdrop, 'none', 'acrylic 降级新增了 backdrop-filter（生产约束）');
      eq(fallback.webkitBackdrop, 'none', 'acrylic 降级新增了 -webkit-backdrop-filter（生产约束）');
      includes(fallback.shadow, 'inset', 'acrylic=false 面板缺少内阴影');
      ok(fallback.borderAlpha > 0.18, 'acrylic=false 边框未强化：alpha=' + fallback.borderAlpha);

      /* 4) acrylic=true 恢复玻璃（不作为生产约束，但保证属性可逆） */
      const restored = await page.evaluate(() => {
        window.__pbMock.appState({
          pinned: false, platform: { windows11: true, build: 22631 },
          build: 'mock', version: '0.1.0', acrylic: true
        });
        return {
          acrylic: document.documentElement.dataset.acrylic,
          bg: getComputedStyle(document.getElementById('panel')).backgroundColor
        };
      });
      eq(restored.acrylic, 'on', 'acrylic=true 未恢复 html[data-acrylic="on"]');
      eq(restored.bg, 'rgba(0, 0, 0, 0)', 'acrylic=true 后实底降级未撤销：' + restored.bg);
    } finally {
      await page.close();
    }

    /* 5) 隐藏态（WebView 暂停）：app-state pinned 只记 D.pendingPin，不写 DOM、不回调 invoke */
    const hidden = await openPage('?mock&freeze&effects=css&hide');
    try {
      await hidden.waitForFunction(() => window.__pbDebug.hidden === true, null, { timeout: 5000 });
      await sleep(120);
      const before = await hidden.evaluate(() => ({
        renders: window.__pbDebug.renders,
        domWrites: window.__pbDebug.domWritesWhileHidden,
        pinOn: document.getElementById('btn-pin').classList.contains('is-on'),
        calls: window.__pbBridge.calls.filter(c => c.cmd === 'set_pinned').length
      }));
      await hidden.evaluate(() => {
        window.__pbMock.appState({
          pinned: true, platform: { windows11: true, build: 22631 },
          build: 'mock', version: '0.1.0', acrylic: false
        });
      });
      await sleep(150);
      const during = await hidden.evaluate(() => ({
        renders: window.__pbDebug.renders,
        domWrites: window.__pbDebug.domWritesWhileHidden,
        pinOn: document.getElementById('btn-pin').classList.contains('is-on'),
        pendingPin: window.__pbDebug.pendingPin,
        calls: window.__pbBridge.calls.filter(c => c.cmd === 'set_pinned').length,
        stored: JSON.parse(localStorage.getItem('pb.settings.v1') || '{}').pinned,
        acrylic: document.documentElement.dataset.acrylic
      }));
      eq(during.renders, before.renders, '隐藏态 app-state 触发了 DOM 渲染');
      eq(during.domWrites, 0, '隐藏态 app-state 写入了 DOM');
      eq(during.calls, before.calls, '隐藏态 app-state 又 invoke set_pinned');
      eq(during.pinOn, before.pinOn, '隐藏态 app-state 直接改动了 pin 按钮');
      eq(during.pendingPin, true, '隐藏态 pinned 未记录到 D.pendingPin');
      eq(during.stored, true, '隐藏态 pinned 未持久化');
      eq(during.acrylic, 'off', '隐藏态（显示前）acrylic=false 未先落 dataset');

      /* 恢复显示：pin UI 由 render() 统一回写 */
      await hidden.evaluate(() => window.__pbMock.visibility(false));
      await hidden.waitForFunction(() => window.__pbDebug.hidden === false, null, { timeout: 3000 });
      await sleep(120);
      const resumed = await hidden.evaluate(() => ({
        pinOn: document.getElementById('btn-pin').classList.contains('is-on'),
        renders: window.__pbDebug.renders,
        acrylic: document.documentElement.dataset.acrylic
      }));
      ok(resumed.pinOn, '恢复显示后 pin 按钮未回写');
      ok(resumed.renders > before.renders, '恢复显示后未渲染');
      eq(resumed.acrylic, 'off', '恢复显示后 acrylic 降级样式丢失');
    } finally {
      await hidden.close();
    }
  });

  /* ============ A8. Tauri-like 下的 pin 契约（A12） ============ */
  await test('A8 模拟 Tauri：tray-action toggle-pin 前端不翻转，app-state 为唯一 pin 事实源', async () => {
    const page = await openTauriLikePage('?mock&freeze');
    try {
      const env = await page.evaluate(() => ({
        hasTauri: window.PB.bridge.hasTauri,
        dataTauri: document.documentElement.dataset.tauri
      }));
      eq(env.hasTauri, true, '模拟 Tauri 环境未生效（bridge.hasTauri 仍为 false）');
      eq(env.dataTauri, 'true', 'html[data-tauri] 未标记为 true');

      /* 托盘 pin：Rust 已 set_pinned + emit app-state；前端收到 tray-action 不得再翻转 */
      const tray = await page.evaluate(() => {
        const count = () => (window.__tauriCalls || []).filter(c => c.cmd === 'set_pinned').length;
        const pinBefore = document.getElementById('btn-pin').classList.contains('is-on');
        const callsBefore = count();
        window.__pbMock.trayAction('toggle-pin');
        return {
          callsBefore: callsBefore,
          callsAfter: count(),
          pinBefore: pinBefore,
          pinAfter: document.getElementById('btn-pin').classList.contains('is-on')
        };
      });
      eq(tray.callsAfter, tray.callsBefore, 'Tauri 下 tray-action toggle-pin 仍 invoke set_pinned（会与 Rust 双翻转）');
      eq(tray.pinAfter, tray.pinBefore, 'Tauri 下 tray-action toggle-pin 仍自行改 pin UI');

      /* Rust emit app-state(pinned=true)：UI 同步，且不产生 set_pinned invoke（无反馈环） */
      const sync = await page.evaluate(() => {
        const count = () => (window.__tauriCalls || []).filter(c => c.cmd === 'set_pinned').length;
        const callsBefore = count();
        window.__pbMock.appState({
          pinned: true, platform: { windows11: true, build: 22631 },
          build: 'mock', version: '0.1.0', acrylic: false
        });
        const m = getComputedStyle(document.getElementById('panel')).backgroundColor.match(/rgba?\(([^)]+)\)/);
        const parts = m ? m[1].split(',').map(Number) : [];
        return {
          callsBefore: callsBefore,
          callsAfter: count(),
          pinOn: document.getElementById('btn-pin').classList.contains('is-on'),
          acrylic: document.documentElement.dataset.acrylic,
          bgAlpha: parts.length === 4 ? parts[3] : 1
        };
      });
      eq(sync.callsAfter, sync.callsBefore, 'Tauri 下 app-state pinned 又 invoke set_pinned（反馈环）');
      ok(sync.pinOn, 'Tauri 下 app-state pinned=true 未回写 pin UI');
      eq(sync.acrylic, 'off', 'Tauri 下 app-state acrylic=false 未落 dataset');
      eq(sync.bgAlpha, 1, 'Tauri 下 acrylic=false 面板未实底降级');

      /* 模拟 Rust emit pinned=false：UI 跟随，仍然不产生 set_pinned invoke */
      await page.evaluate(() => {
        window.__pbMock.appState({
          pinned: false, platform: { windows11: true, build: 22631 },
          build: 'mock', version: '0.1.0', acrylic: true
        });
      });
      const off = await page.evaluate(() => ({
        pinOn: document.getElementById('btn-pin').classList.contains('is-on'),
        calls: (window.__tauriCalls || []).filter(c => c.cmd === 'set_pinned').length
      }));
      ok(!off.pinOn, 'Tauri 下 app-state pinned=false 未回写 pin UI');
      eq(off.calls, tray.callsBefore, 'Tauri 下 pin 同步链路出现额外 set_pinned invoke');

      const tauriErrors = pageErrors.filter(e => e.page === 'tauri-like:?mock&freeze');
      eq(tauriErrors.length, 0, '模拟 Tauri 页面出现控制台错误：' + JSON.stringify(tauriErrors.slice(0, 3)));
    } finally {
      await page.close();
    }
  });

  /* ============ A9. 备选 B：生产窗口 = 面板 720×380（§10.3） ============ */
  await test('A9 720×380 生产视口：body padding=0、stage/panel 填满窗口、内部元素不出界', async () => {
    /* 生产（无 ?effects=css）：窗口 = 面板，720×380 视口应被 .stage/.panel 正好填满 */
    const prod = await openPage('?demo', 720, 380);
    try {
      await waitIntroDone(prod);
      const info = await prod.evaluate(() => {
        const rect = sel => {
          const node = document.querySelector(sel);
          if (!node) return null;
          const r = node.getBoundingClientRect();
          return { x: r.x, y: r.y, w: r.width, h: r.height, right: r.right, bottom: r.bottom };
        };
        const bodyCs = getComputedStyle(document.body);
        const panelCs = getComputedStyle(document.getElementById('panel'));
        return {
          vw: window.innerWidth,
          vh: window.innerHeight,
          bodyPadding: [bodyCs.paddingTop, bodyCs.paddingRight, bodyCs.paddingBottom, bodyCs.paddingLeft],
          docOverflowX: document.documentElement.scrollWidth > window.innerWidth,
          docOverflowY: document.documentElement.scrollHeight > window.innerHeight,
          stage: rect('.stage'),
          panel: rect('#panel'),
          glow: rect('#glow-layer'),
          controls: rect('#controls'),
          hero: rect('#hero'),
          side: rect('.side'),
          foot: rect('.side-foot'),
          list: rect('#list'),
          panelRadius: panelCs.borderTopLeftRadius,
          panelBackdrop: panelCs.backdropFilter
        };
      });
      eq(info.vw, 720, '生产视口宽度应为 720');
      eq(info.vh, 380, '生产视口高度应为 380');
      eq(info.bodyPadding.join(','), '0px,0px,0px,0px', 'body padding 应为 0（备选 B 无安全边）');
      ok(!info.docOverflowX && !info.docOverflowY, '720×380 生产视口出现滚动溢出');
      eq(info.panelBackdrop, 'none', '生产模式 720×380 不应出现 backdrop-filter');
      const fills = r => !!r && Math.abs(r.x) < 0.5 && Math.abs(r.y) < 0.5 &&
        Math.abs(r.w - 720) < 0.5 && Math.abs(r.h - 380) < 0.5;
      ok(fills(info.stage), '.stage 未填满 720×380 窗口：' + JSON.stringify(info.stage));
      ok(fills(info.panel), '.panel 未填满 720×380 窗口：' + JSON.stringify(info.panel));
      ok(fills(info.glow), 'glow-layer 未与面板/窗口对齐：' + JSON.stringify(info.glow));
      eq(info.panelRadius, '12px', '面板圆角应为 12px（窗口=面板时贴近 DWM 圆角）');
      const inside = (r, label) => {
        ok(!!r, label + ' 不存在');
        ok(r.x >= -0.5 && r.y >= -0.5 && r.right <= 720.5 && r.bottom <= 380.5,
          label + ' 越出 720×380 窗口：' + JSON.stringify(r));
      };
      inside(info.controls, '控件簇');
      inside(info.hero, '主卡');
      inside(info.side, '列表区');
      inside(info.foot, '列表 footer');
      inside(info.list, '列表滚动区');
      await screenshot(prod, 'ui-prod-720x380.png');

      /* 抽屉 / toast 是绝对定位覆盖层，720×380 下也不能越界。
         注意用 demo 自己的 session 发 alert，避免 mock 默认 session 触发全量重置。 */
      await prod.evaluate(() => {
        document.getElementById('btn-settings').click();
        window.__pbMock.emit({
          t: 'alert', session: window.__pbDebug.session, kind: 'low',
          key: 'headset:arctis-nova-7', level: 12, text: 'Arctis Nova 7 电量低，建议充电'
        });
      });
      await sleep(420);
      const overlay = await prod.evaluate(() => {
        const pick = sel => {
          const node = document.querySelector(sel);
          if (!node) return null;
          const r = node.getBoundingClientRect();
          return { x: r.x, y: r.y, right: r.right, bottom: r.bottom };
        };
        return { drawer: pick('#drawer'), toast: pick('.toast') };
      });
      const inWindow = r => !!r && r.x >= -0.5 && r.y >= -0.5 && r.right <= 720.5 && r.bottom <= 380.5;
      ok(inWindow(overlay.drawer), '抽屉越出 720×380 窗口：' + JSON.stringify(overlay.drawer));
      ok(inWindow(overlay.toast), 'toast 越出 720×380 窗口：' + JSON.stringify(overlay.toast));
      await screenshot(prod, 'ui-prod-720x380-overlay.png');
    } finally {
      await prod.close();
    }

    /* ?effects=css 浏览器评审模式在 720×380 生产视口下仍能正常看图（4 处模糊） */
    const dev = await openPage('?demo&effects=css', 720, 380);
    try {
      await waitIntroDone(dev);
      const devInfo = await dev.evaluate(() => {
        const sel = s => getComputedStyle(document.querySelector(s)).backdropFilter;
        const panel = document.getElementById('panel').getBoundingClientRect();
        return {
          panel: sel('#panel'),
          controls: sel('#controls'),
          hero: sel('.hero-card'),
          row: sel('.row'),
          bodyPadding: getComputedStyle(document.body).paddingTop,
          panelRect: { x: panel.x, y: panel.y, w: panel.width, h: panel.height }
        };
      });
      ok(devInfo.panel !== 'none', '720×380 评审模式面板无 backdrop-filter');
      ok(devInfo.controls !== 'none', '720×380 评审模式控件簇无 backdrop-filter');
      ok(devInfo.hero !== 'none', '720×380 评审模式主卡无 backdrop-filter');
      ok(devInfo.row !== 'none', '720×380 评审模式列表行无 backdrop-filter');
      eq(devInfo.bodyPadding, '0px', '720×380 评审模式 body padding 应仍为 0');
      eq(Math.round(devInfo.panelRect.w), 720, '720×380 评审模式面板宽度');
      eq(Math.round(devInfo.panelRect.h), 380, '720×380 评审模式面板高度');
      await screenshot(dev, 'ui-dev-720x380-effects.png');
    } finally {
      await dev.close();
    }
  });

  /* ============ 汇总 ============ */
  await browser.close();
  await new Promise(resolve => srv.server.close(resolve));

  console.log('');
  console.log('  ' + passed + ' passed, ' + failures.length + ' failed');
  if (failures.length) {
    console.log('  失败项：');
    failures.forEach(f => console.log('   - ' + f.name + '：' + f.message));
    process.exitCode = 1;
  }
})().catch(async err => {
  console.error('测试运行器异常：', err);
  try { if (browser) await browser.close(); } catch (e) { /* ignore */ }
  process.exitCode = 1;
});
