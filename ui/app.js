/* ============================================================
   app.js — 外设电量面板（无框架静态前端）
   · keyed DOM diff：行只建一次，仅 patch 文本/class/进度
   · 协议状态机：hello/device/gone/tick/alert/stat + session/seq
   · level=null：空心环 + 未知/approx，不按低电判红
   · 隐藏四步（前端部分）：pause WAAPI / 停 rAF / 清 timer / 不写 DOM，
     恢复时先用最后快照渲染一帧
   · 设置抽屉（低电阈值 / 降低动效 / 固定 / 版本）存 localStorage
   ============================================================ */
(function () {
  'use strict';

  const bridge = window.PB.bridge;
  const bridgeDebug = window.__pbBridge;
  const root = document.documentElement;

  /* ========================================================== 常量 */

  const ICONS = {
    headset: '<path d="M5 15.2v-3.4a7 7 0 0 1 14 0v3.4"/><rect x="3" y="14" width="4.8" height="6.6" rx="2.4"/><rect x="16.2" y="14" width="4.8" height="6.6" rx="2.4"/>',
    mouse: '<rect x="6.2" y="2.4" width="11.6" height="19.2" rx="5.8"/><path d="M12 6.6v3.8"/>',
    keyboard: '<rect x="2" y="6" width="20" height="12" rx="2.8"/><path d="M6.2 10h.01M9.9 10h.01M13.6 10h.01M17.3 10h.01M8 14.2h8"/>',
    gamepad: '<path d="M7.6 7h8.8a4.5 4.5 0 0 1 4.4 5.4l-.6 3a2.6 2.6 0 0 1-4.6 1L14 15h-4l-1.6 1.4a2.6 2.6 0 0 1-4.6-1l-.6-3A4.5 4.5 0 0 1 7.6 7Z"/><path d="M8 11.2v2M7 12.2h2M16.2 11.6h.01M18.4 13.2h.01"/>',
    bluetooth: '<path d="M7 7l10 10-5 4V3l5 4L7 17"/>'
  };

  const TRANSPORT_TEXT = {
    '2.4g': '2.4 GHz',
    'usb': 'USB',
    'bluetooth': '蓝牙',
    'unknown': '未知连接'
  };

  const RING_C = 2 * Math.PI * 45;
  const SETTINGS_KEY = 'pb.settings.v1';
  const INTRO_MS = 1150;
  const TOAST_MS = 4200;
  const STATE_CLASSES = ['state-normal', 'state-low', 'state-amber', 'state-charging', 'state-offline', 'state-unknown', 'state-empty'];

  /* ========================================================== 设置 */

  function loadSettings() {
    const def = { lowThreshold: 15, reducedMotion: false, pinned: false };
    try {
      const raw = localStorage.getItem(SETTINGS_KEY);
      if (!raw) return def;
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object') return def;
      return {
        lowThreshold: clamp(Number(parsed.lowThreshold) || def.lowThreshold, 5, 50),
        reducedMotion: !!parsed.reducedMotion,
        pinned: !!parsed.pinned
      };
    } catch (e) {
      return def;
    }
  }

  function saveSettings() {
    try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch (e) { /* ignore */ }
  }

  const settings = loadSettings();

  /* ========================================================== 状态 */

  const state = {
    session: null,
    seq: -1,
    devices: new Map(),
    order: [],
    heroKey: null,
    heroPinnedByUser: false,
    pinnedHeroKey: null,
    morphFrom: null,
    pinned: !!settings.pinned,
    platform: { windows11: true, build: 0 },
    appVersion: '',
    build: '',
    protocol: 1,
    collectorState: { state: 'starting' },
    versionError: false,
    hidden: false,
    dirty: false,
    lastUpdate: 0,
    helloReceived: false,
    lastSnapshotRequest: 0
  };

  /* 隐藏期间收到的用户动作只记状态，等窗口恢复显示后再写 DOM / 播动画 */
  const pendingUi = { settings: false, spin: false };

  /* ========================================================== 调试计数（供 Playwright 读取） */

  const D = {
    mode: bridge.scenario || bridge.debug.mode,
    scenario: bridge.scenario || '',
    renders: 0,
    renderSkipsHidden: 0,
    rowCreates: 0,
    rowUpdates: 0,
    rowRemoves: 0,
    heroUpdates: 0,
    domWritesWhileHidden: 0,
    hidden: false,
    hiddenEvents: 0,
    pausedAnimations: 0,
    rafTicks: 0,
    rafActive: false,
    timersActive: 0,
    sessions: 0,
    session: null,
    seq: -1,
    events: 0,
    dropped: 0,
    badEvents: 0,
    introPlays: 0,
    introActive: false,
    toasts: 0,
    lastAlert: null,
    trayCalls: [],
    lastTray: null,
    invokes: [],
    invokeErrors: [],
    settings: null,
    effects: root.dataset.effects || 'none',
    reducedMotion: false,
    devices: 0,
    rowCount: 0,
    rowKeys: [],
    allRowKeys: [],
    heroRowKey: null,
    trayPrimed: false,
    hiddenActionsBlocked: 0,
    /* P1-09：生产面板的浏览器右键菜单 / 加速键拦截计数（Playwright 断言用） */
    blockedShortcuts: 0,
    lastBlockedShortcut: '',
    blockedContextMenus: 0,
    /* P2-46 / §10.3：app-state 下发的 acrylic 可用性 → html[data-acrylic] */
    acrylic: '',
    pendingSettings: false,
    pendingSpin: false,
    deviceKeys: [],
    heroKey: null,
    lowThreshold: settings.lowThreshold,
    lastRenderAt: 0,
    snapshotAt: 0
  };
  window.__pbDebug = D;

  /* ========================================================== DOM */

  const el = {};
  function cacheDom() {
    [
      'stage', 'panel', 'glow-layer', 'specular', 'list', 'empty', 'banner', 'banner-text',
      'subtitle', 'live', 'live-text', 'hero', 'hero-ring', 'hero-num', 'hero-pct',
      'hero-caption', 'hero-name', 'hero-icon', 'hero-transport', 'hero-alert',
      'btn-refresh', 'btn-pin', 'btn-settings', 'btn-min', 'btn-close', 'btn-restart',
      'btn-all', 'btn-drawer-close', 'drawer', 'drawer-scrim', 'toasts', 'ripple-layer',
      'set-threshold', 'set-threshold-out', 'set-reduced', 'set-pinned',
      'info-build', 'info-protocol', 'info-version', 'info-platform', 'info-session'
    ].forEach(function (id) {
      el[id] = document.getElementById(id);
    });
    el.heroCard = el['hero-ring'] ? el['hero-ring'].closest('.hero-card') : null;
    el.heroRingSvg = el['hero-ring'] ? el['hero-ring'].querySelector('svg') : null;
    el.heroFill = el['hero-ring'] ? el['hero-ring'].querySelector('.fill') : null;
    el.heroLevelBox = el['hero-ring'] ? el['hero-ring'].querySelector('.ring-level') : null;
    el.heroNum = document.getElementById('hero-num');
    el.heroPct = document.getElementById('hero-pct');
    el.heroCaption = document.getElementById('hero-caption');
    el.heroName = document.getElementById('hero-name');
    el.heroIcon = document.getElementById('hero-icon');
    el.heroTransport = document.getElementById('hero-transport');
    el.heroAlert = document.getElementById('hero-alert');
  }

  /* ========================================================== 定时器 / rAF */

  const timers = new Set();
  function setT(fn, ms) {
    const id = window.setTimeout(function () {
      timers.delete(id);
      D.timersActive = timers.size;
      fn();
    }, ms);
    timers.add(id);
    D.timersActive = timers.size;
    return id;
  }
  function clearT(id) {
    if (id == null) return;
    if (timers.has(id)) {
      window.clearTimeout(id);
      timers.delete(id);
      D.timersActive = timers.size;
    }
  }
  function clearAllTimers() {
    timers.forEach(function (id) { window.clearTimeout(id); });
    timers.clear();
    D.timersActive = 0;
  }

  const rafIds = new Set();
  function raf(fn) {
    const id = window.requestAnimationFrame(function (t) {
      rafIds.delete(id);
      D.rafActive = rafIds.size > 0;
      D.rafTicks++;
      try { fn(t); } catch (e) { /* 单帧异常不打断后续 */ }
    });
    rafIds.add(id);
    D.rafActive = true;
    return id;
  }
  function cancelRaf(id) {
    if (!id) return;
    window.cancelAnimationFrame(id);
    rafIds.delete(id);
    D.rafActive = rafIds.size > 0;
  }
  function cancelAllRaf() {
    rafIds.forEach(function (id) { window.cancelAnimationFrame(id); });
    rafIds.clear();
    D.rafActive = false;
    specularRaf = 0;
    heroTweenRaf = 0;
  }

  /* ========================================================== 工具函数 */

  function clamp(n, lo, hi) { return Math.min(hi, Math.max(lo, n)); }

  function levelOf(d) {
    if (!d || d.level == null) return null;
    const n = Number(d.level);
    return Number.isFinite(n) ? clamp(n, 0, 100) : null;
  }

  function threshold() { return settings.lowThreshold; }

  function prefersReducedMotion() {
    if (settings.reducedMotion) return true;
    try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; }
  }

  function transportText(t) { return TRANSPORT_TEXT[t] || String(t || '未知连接'); }

  function humanLeft(sec) {
    if (sec == null) return '';
    const s = Math.max(0, Math.round(Number(sec) || 0));
    if (s <= 0) return '即将耗尽';
    if (s < 60) return '不足 1 分钟';
    const minutes = Math.round(s / 60);
    if (s < 3600) return '约 ' + Math.max(1, minutes) + ' 分钟';
    const hours = Math.floor(s / 3600);
    const mins = Math.round((s % 3600) / 60);
    if (hours < 24) return mins > 0 ? ('约 ' + hours + ' 小时 ' + mins + ' 分') : ('约 ' + hours + ' 小时');
    const days = Math.floor(s / 86400);
    const rh = Math.round((s % 86400) / 3600);
    return rh > 0 ? ('约 ' + days + ' 天 ' + rh + ' 小时') : ('约 ' + days + ' 天');
  }

  function statusText(d) {
    if (!d) return '';
    if (d.online === false) return '离线';
    if (d.charging) return '充电中';
    if (levelOf(d) == null) return d.approx || '电量未知';
    if (d.left_s != null) return humanLeft(d.left_s);
    if (d.approx) return d.approx;
    return '剩余时间未知';
  }

  function ringStateClass(d) {
    if (!d) return 'state-empty';
    if (d.online === false) return 'state-offline';
    if (d.charging) return 'state-charging';
    if (levelOf(d) == null) return 'state-unknown';
    const lv = levelOf(d);
    if (lv <= threshold()) return 'state-low';
    if (lv <= 35) return 'state-amber';
    return 'state-normal';
  }

  function setRingState(ringEl, d) {
    const cls = ringStateClass(d);
    for (let i = 0; i < STATE_CLASSES.length; i++) {
      if (STATE_CLASSES[i] !== cls) ringEl.classList.remove(STATE_CLASSES[i]);
    }
    ringEl.classList.add(cls);
  }

  function iconHTML(kind, cls) {
    const p = ICONS[kind] || ICONS.bluetooth;
    return '<svg class="' + (cls || 'kind-ico') + '" viewBox="0 0 24 24" aria-hidden="true">' + p + '</svg>';
  }

  function kindIconHTML(kind) { return iconHTML(kind, 'kind-ico'); }

  function displayLevelText(d) {
    const lv = levelOf(d);
    if (lv == null) return d.approx || '未知';
    return Math.round(lv) + '%';
  }

  function ariaLabel(d) {
    const lv = levelOf(d);
    const area = lv == null ? (d.approx || '电量未知') : (Math.round(lv) + '%');
    let extra = '';
    if (d.online === false) extra = '，离线';
    else if (d.charging) extra = '，充电中';
    return d.name + '，' + area + extra;
  }

  function deviceName(key) {
    const d = state.devices.get(key);
    return d ? d.name : '设备';
  }

  /* ========================================================== 圆环 DOM */

  const SVG_NS = 'http://www.w3.org/2000/svg';

  function buildRing(size) {
    const ring = document.createElement('div');
    ring.className = 'ring ' + (size === 'lg' ? 'ring-lg' : 'ring-sm');
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', '0 0 100 100');
    svg.setAttribute('aria-hidden', 'true');
    ['track', 'glow', 'fill'].forEach(function (cls) {
      const c = document.createElementNS(SVG_NS, 'circle');
      c.setAttribute('cx', '50');
      c.setAttribute('cy', '50');
      c.setAttribute('r', '45');
      c.setAttribute('class', cls);
      svg.appendChild(c);
    });
    ring.appendChild(svg);
    const center = document.createElement('div');
    center.className = 'ring-center';
    ring.appendChild(center);
    return { ring: ring, svg: svg, fill: svg.querySelector('.fill'), center: center };
  }

  function buildGlyph() {
    const span = document.createElement('span');
    span.className = 'ring-glyph-wrap';
    return span;
  }

  /* ========================================================== 行创建 / 更新 */

  const rowMap = new Map(); /* key -> { el, ring, fill, glyph, name, subIcon, transport, dot, left, level, cache } */

  function markHiddenWrite(where) {
    if (state.hidden) {
      D.domWritesWhileHidden++;
      D.hiddenWriteWhere = where;
    }
  }

  function createRow(d) {
    markHiddenWrite('createRow');
    D.rowCreates++;
    const rec = {};
    const row = document.createElement('div');
    row.className = 'row';
    row.setAttribute('role', 'button');
    row.tabIndex = 0;
    row.dataset.key = d.key;

    const ring = buildRing('sm');
    const glyph = buildGlyph();
    ring.center.appendChild(glyph);
    rec.ring = ring.ring;
    rec.ringBox = ring;
    rec.fill = ring.fill;
    rec.glyph = glyph;

    const main = document.createElement('div');
    main.className = 'row-main';
    const name = document.createElement('div');
    name.className = 'row-name';
    const sub = document.createElement('div');
    sub.className = 'row-sub';
    const subIcon = document.createElement('span');
    subIcon.className = 'row-kind';
    subIcon.innerHTML = kindIconHTML(d.kind);
    const transport = document.createElement('span');
    transport.className = 'row-transport';
    const dot = document.createElement('span');
    dot.className = 'dot';
    dot.textContent = '·';
    const left = document.createElement('span');
    left.className = 'row-left';
    sub.appendChild(subIcon);
    sub.appendChild(transport);
    sub.appendChild(dot);
    sub.appendChild(left);
    main.appendChild(name);
    main.appendChild(sub);

    const right = document.createElement('div');
    right.className = 'row-right';
    const level = document.createElement('span');
    level.className = 'row-level';
    const caret = document.createElementNS(SVG_NS, 'svg');
    caret.setAttribute('class', 'caret');
    caret.setAttribute('viewBox', '0 0 24 24');
    caret.setAttribute('aria-hidden', 'true');
    const caretPath = document.createElementNS(SVG_NS, 'path');
    caretPath.setAttribute('d', 'M9 18l6-6-6-6');
    caret.appendChild(caretPath);
    right.appendChild(level);
    right.appendChild(caret);

    row.appendChild(rec.ring);
    row.appendChild(main);
    row.appendChild(right);

    rec.el = row;
    rec.name = name;
    rec.subIcon = subIcon;
    rec.transport = transport;
    rec.dotEl = dot;
    rec.left = left;
    rec.level = level;
    rec.cache = { hero: false };

    row.addEventListener('click', function () { selectHero(row.dataset.key, false); });
    row.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
        e.preventDefault();
        selectHero(row.dataset.key, true);
      }
    });

    return rec;
  }

  function patchRow(rec, d, isHero) {
    markHiddenWrite('patchRow');
    D.rowUpdates++;
    const cache = rec.cache;

    /* P3-15：主卡行保留同一 DOM 节点，仅用 hidden/class 切换，
       这样 hero 换人时不重建行、不影响其他行的 hover/focus。 */
    const heroNow = !!isHero;
    if (cache.hero !== heroNow) {
      rec.el.classList.toggle('is-hero', heroNow);
      rec.el.hidden = heroNow;
      rec.el.tabIndex = heroNow ? -1 : 0;
      if (heroNow) rec.el.setAttribute('aria-hidden', 'true');
      else rec.el.removeAttribute('aria-hidden');
      cache.hero = heroNow;
      if (heroNow && !state.hidden &&
        (document.activeElement === rec.el || rec.el.contains(document.activeElement))) {
        const firstVisible = el.list.querySelector('.row:not([hidden])');
        if (firstVisible) { try { firstVisible.focus(); } catch (e) { /* ignore */ } }
      }
    }

    if (cache.kind !== d.kind) {
      rec.subIcon.innerHTML = iconHTML(d.kind, 'kind-ico');
      if (rec.glyph) rec.glyph.innerHTML = iconHTML(d.kind, 'ring-glyph');
      cache.kind = d.kind;
    }

    if (cache.name !== d.name) {
      rec.name.textContent = d.name;
      rec.el.dataset.name = d.name;
      cache.name = d.name;
    }

    const tr = transportText(d.transport);
    if (cache.transport !== tr) {
      rec.transport.textContent = tr;
      cache.transport = tr;
    }

    const st = statusText(d);
    if (cache.status !== st) {
      rec.left.textContent = st;
      cache.status = st;
    }
    rec.dotEl.hidden = !st;

    const lvText = displayLevelText(d);
    if (cache.levelText !== lvText) {
      rec.level.textContent = lvText;
      cache.levelText = lvText;
    }
    const unknown = levelOf(d) == null;
    const lvCls = unknown ? (d.approx ? 'is-approx' : 'is-unknown') : '';
    if (cache.levelCls !== lvCls) {
      rec.level.classList.toggle('is-approx', lvCls === 'is-approx');
      rec.level.classList.toggle('is-unknown', lvCls === 'is-unknown');
      cache.levelCls = lvCls;
    }

    const lv = levelOf(d);
    const off = lv == null ? RING_C : RING_C * (1 - lv / 100);
    const offKey = off.toFixed(2);
    if (cache.offset !== offKey) {
      rec.fill.style.setProperty('stroke-dashoffset', offKey);
      cache.offset = offKey;
    }

    const stCls = ringStateClass(d);
    if (cache.stateCls !== stCls) {
      setRingState(rec.ring, d);
      cache.stateCls = stCls;
    }

    const offlineNow = d.online === false;
    if (cache.offline !== offlineNow) {
      rec.el.classList.toggle('is-offline', offlineNow);
      cache.offline = offlineNow;
    }

    const al = ariaLabel(d);
    if (cache.aria !== al) {
      rec.el.setAttribute('aria-label', al);
      cache.aria = al;
    }
  }

  function syncRows(desired, heroKey) {
    markHiddenWrite('syncRows');
    const seen = new Set();
    let visibleCount = 0;
    desired.forEach(function (d) {
      let rec = rowMap.get(d.key);
      if (!rec) {
        rec = createRow(d);
        rowMap.set(d.key, rec);
        el.list.appendChild(rec.el);
      }
      const isHero = !!heroKey && d.key === heroKey;
      patchRow(rec, d, isHero);
      if (!isHero) visibleCount++;
      seen.add(d.key);
    });

    /* 删除已不在的设备行：只 remove 节点，不重建列表 */
    rowMap.forEach(function (rec, key) {
      if (!seen.has(key)) {
        markHiddenWrite('removeRow');
        rec.el.remove();
        rowMap.delete(key);
        D.rowRemoves++;
      }
    });

    /* 稳定顺序：按 state.order（首次出现顺序）重排，节点原地移动 */
    let anchor = el.list.firstChild;
    desired.forEach(function (d) {
      const rec = rowMap.get(d.key);
      if (!rec) return;
      const node = rec.el;
      if (node === anchor) {
        anchor = anchor.nextSibling;
      } else {
        el.list.insertBefore(node, anchor);
      }
    });

    return visibleCount;
  }

  /* ========================================================== 主卡 */

  const lastHero = { key: null, level: null, name: '' };
  let heroTweenRaf = 0;
  let heroTweenToken = 0;
  let heroCardAnim = null;

  function renderHero(hero) {
    markHiddenWrite('renderHero');
    D.heroUpdates++;
    const ring = el['hero-ring'];
    setRingState(ring, hero);

    const lv = hero ? levelOf(hero) : null;
    const lvText = !hero ? '—' : (lv == null ? (hero.approx || '未知') : String(Math.round(lv)));
    el.heroNum.textContent = lvText;
    el.heroLevelBox.classList.toggle('is-text', !!hero && lv == null);
    el.heroLevelBox.classList.toggle('is-empty', !hero);
    el.heroPct.hidden = !hero || lv == null;

    let caption = '等待设备';
    if (hero) {
      if (hero.online === false) caption = '设备离线';
      else if (hero.charging) caption = '充电中';
      else if (lv == null) caption = hero.approx ? '电量未知（档位）' : '电量未知';
      else if (hero.left_s != null) caption = humanLeft(hero.left_s);
      else if (hero.approx) caption = hero.approx;
      else caption = '剩余时间未知';
    }
    el.heroCaption.textContent = caption;

    const name = hero ? hero.name : '等待设备';
    el.heroName.textContent = name;

    const kind = hero ? hero.kind : '';
    if (el.heroIcon.dataset.kind !== kind) {
      el.heroIcon.innerHTML = hero ? kindIconHTML(kind) : '';
      el.heroIcon.dataset.kind = kind;
    }

    el.heroTransport.textContent = hero ? transportText(hero.transport) : '—';

    const low = !!hero && hero.online !== false && !hero.charging && lv != null && lv <= threshold();
    el.heroAlert.hidden = !low;
    el.panel.classList.toggle('is-low', low);

    const off = lv == null ? RING_C : RING_C * (1 - lv / 100);
    el.heroFill.style.setProperty('stroke-dashoffset', off.toFixed(2));

    if (hero) el.hero.setAttribute('aria-label', '主设备：' + ariaLabel(hero));

    const morph = hero && lastHero.key && state.morphFrom === lastHero.key;
    const changed = !!hero && !!lastHero.key && !morph && hero.key !== lastHero.key;
    if (morph) state.morphFrom = null;

    if (changed && !prefersReducedMotion() && !state.hidden) {
      /* 主卡换人：卡片轻微回弹 + 数字滚动（圆环由 CSS transition 从旧值扫过去） */
      if (el.heroCard && el.heroCard.animate) {
        try {
          if (heroCardAnim) { try { heroCardAnim.cancel(); } catch (e) { /* ignore */ } }
          heroCardAnim = el.heroCard.animate(
            [{ opacity: 0.55, transform: 'scale(0.975)' }, { opacity: 1, transform: 'scale(1)' }],
            { duration: 320, easing: 'cubic-bezier(.22,.61,.36,1)' }
          );
        } catch (e) { /* ignore */ }
      }
      tweenHeroNumber(lastHero.level, lv);
    } else if (!hero || lv == null) {
      cancelHeroTween();
    }

    lastHero.key = hero ? hero.key : null;
    lastHero.level = lv;
    lastHero.name = name;
  }

  function cancelHeroTween() {
    heroTweenToken++;
    if (heroTweenRaf) { cancelRaf(heroTweenRaf); heroTweenRaf = 0; }
  }

  function tweenHeroNumber(from, to) {
    cancelHeroTween();
    if (from == null || to == null || prefersReducedMotion()) return;
    const a = clamp(from, 0, 100);
    const b = clamp(to, 0, 100);
    const token = heroTweenToken;
    const t0 = performance.now();
    const dur = 620;
    const tick = function (t) {
      if (token !== heroTweenToken || state.hidden) { heroTweenRaf = 0; return; }
      const k = Math.min(1, (t - t0) / dur);
      const eased = 1 - Math.pow(1 - k, 3);
      el.heroNum.textContent = String(Math.round(a + (b - a) * eased));
      if (k < 1) heroTweenRaf = raf(tick);
      else heroTweenRaf = 0;
    };
    heroTweenRaf = raf(tick);
  }

  /* ========================================================== 状态 / 副标题 / 横幅 / 托盘 */

  let lastTrayJson = '';
  /* P1-2：首次拿到设备数据之前保持 Rust 的「未知」托盘图标，不要下发 offline。
     有数据之后（含之后全部 gone）才同步，数据语义变化时立即更新。 */
  let trayPrimed = false;

  function syncTray() {
    if (state.devices.size === 0 && !trayPrimed) return;
    const online = [];
    state.order.forEach(function (key) {
      const d = state.devices.get(key);
      if (d && d.online !== false) online.push(d);
    });
    let payload;
    if (online.length) {
      let target = online[0];
      let best = levelOf(target) == null ? Infinity : levelOf(target);
      for (let i = 1; i < online.length; i++) {
        const lv = levelOf(online[i]);
        const v = lv == null ? Infinity : lv;
        if (v < best) { best = v; target = online[i]; }
      }
      payload = {
        level: levelOf(target) == null ? null : levelOf(target),
        charging: !!target.charging,
        online: true,
        kind: target.kind || ''
      };
    } else {
      payload = { level: null, charging: false, online: false, kind: '' };
    }
    if (state.devices.size > 0) trayPrimed = true;
    D.trayPrimed = trayPrimed;
    const json = JSON.stringify(payload);
    if (json !== lastTrayJson) {
      lastTrayJson = json;
      D.lastTray = payload;
      D.trayCalls.push(payload);
      if (D.trayCalls.length > 80) D.trayCalls.shift();
      /* 只走 invoke，不写 DOM、不新建动画 —— 隐藏态也安全（§3.1 第 3 条） */
      bridge.call('set_tray_state', payload);
    }
  }

  function renderStatus() {
    const cs = state.collectorState && state.collectorState.state;
    const onlineCount = countOnline();
    const now = new Date();
    const hh = String(now.getHours()).padStart(2, '0');
    const mm = String(now.getMinutes()).padStart(2, '0');
    let liveCls = '';
    let liveText = '在线';
    let subtitle = '';

    if (state.versionError) {
      liveCls = 'is-error';
      liveText = '版本过期';
      subtitle = '采集器版本与协议不匹配';
    } else if (cs === 'error' || cs === 'exited') {
      liveCls = 'is-error';
      liveText = '已断开';
      subtitle = '采集器已断开 · ' + hh + ':' + mm;
    } else if (cs === 'starting' || !cs) {
      liveCls = 'is-connecting';
      liveText = '连接中';
      subtitle = '正在连接采集器…';
    } else if (state.devices.size === 0) {
      liveCls = 'is-idle';
      liveText = '等待设备';
      subtitle = '未发现设备 · ' + hh + ':' + mm + ' 更新';
    } else {
      liveText = onlineCount > 0 ? '在线' : '离线';
      if (onlineCount === 0) liveCls = 'is-idle';
      subtitle = onlineCount + ' 台设备 · ' + hh + ':' + mm + ' 更新';
    }

    el['live'].className = 'live ' + liveCls;
    el['live-text'].textContent = liveText;
    el.subtitle.textContent = subtitle;

    if (state.versionError) {
      showBanner('采集器版本过期，请重启采集器');
    } else if (cs === 'error') {
      showBanner((state.collectorState && state.collectorState.message) || '采集器启动失败');
    } else if (cs === 'exited') {
      showBanner('采集器已退出');
    } else {
      hideBanner();
    }
  }

  function showBanner(text) {
    el.banner.hidden = false;
    if (el['banner-text'].textContent !== text) el['banner-text'].textContent = text;
  }
  function hideBanner() {
    el.banner.hidden = true;
  }

  function countOnline() {
    let n = 0;
    state.devices.forEach(function (d) { if (d.online !== false) n++; });
    return n;
  }

  function updateInfo() {
    el['info-build'].textContent = state.build || '—';
    el['info-protocol'].textContent = String(state.protocol == null ? '—' : state.protocol);
    el['info-version'].textContent = state.appVersion || '—';
    let plat = state.platform.windows11 ? 'Windows 11' : 'Windows 10';
    if (state.platform.build) plat += ' · ' + state.platform.build;
    if (!bridge.hasTauri) plat = '浏览器预览 · ' + plat;
    el['info-platform'].textContent = plat;
    el['info-session'].textContent = state.session || '—';
  }

  function updatePinUI() {
    if (state.hidden) { state.dirty = true; return; }
    el['btn-pin'].classList.toggle('is-on', state.pinned);
    el['btn-pin'].setAttribute('aria-pressed', String(state.pinned));
    el['set-pinned'].checked = state.pinned;
  }

  /* ========================================================== 渲染主入口 */

  function pickHero() {
    if (state.devices.size === 0) return null;

    /* 用户手动固定（点击列表行）优先；固定设备 gone/offline 时回落到自动 */
    if (state.heroPinnedByUser && state.pinnedHeroKey) {
      const pinned = state.devices.get(state.pinnedHeroKey);
      if (pinned && pinned.online !== false) return pinned;
      state.heroPinnedByUser = false;
      state.pinnedHeroKey = null;
    }

    /* 自动模式：每次渲染都取「在线设备中 level 最低者」。
       level=null 视为 Infinity；同分按 state.order 先出现者优先（lowest 用严格 <）。 */
    const online = onlineDevices();
    if (online.length) return lowest(online);
    return lowest(Array.from(state.devices.values()));
  }

  function onlineDevices() {
    const list = [];
    state.order.forEach(function (key) {
      const d = state.devices.get(key);
      if (d && d.online !== false) list.push(d);
    });
    return list;
  }

  function lowest(list) {
    let best = list[0];
    let bestV = levelOf(best) == null ? Infinity : levelOf(best);
    for (let i = 1; i < list.length; i++) {
      const lv = levelOf(list[i]);
      const v = lv == null ? Infinity : lv;
      if (v < bestV) { bestV = v; best = list[i]; }
    }
    return best;
  }

  function render(reason) {
    if (state.hidden) {
      state.dirty = true;
      D.renderSkipsHidden++;
      /* P1-2：隐藏态托盘是唯一环境指示器。只做轻量状态同步（invoke，不写 DOM、
         不碰 WAAPI/rAF/timer），让托盘电量/充电/在线状态与数据保持一致。 */
      syncTray();
      return;
    }
    D.renders++;
    markHiddenWrite('render');

    const hero = pickHero();
    state.heroKey = hero ? hero.key : null;

    /* P3-15：所有设备都保留一个行节点（主卡那行 hidden），hero 换人只切
       hidden/class，不建/删节点，因此其它行的 hover/focus 不受影响。 */
    const desired = [];
    state.order.forEach(function (key) {
      const d = state.devices.get(key);
      if (d) desired.push(d);
    });
    /* 防御：order 里漏掉的设备也渲染 */
    state.devices.forEach(function (d) {
      if (state.order.indexOf(d.key) >= 0) return;
      desired.push(d);
      state.order.push(d.key);
    });

    renderHero(hero);
    const visibleRows = syncRows(desired, state.heroKey);
    renderStatus();
    syncTray();
    updateInfo();
    updatePinUI();

    el.empty.hidden = state.devices.size !== 0;
    el.list.hidden = state.devices.size === 0 ? true : false;

    D.devices = state.devices.size;
    D.rowCount = visibleRows;
    /* 调试用：按 DOM 顺序暴露可见/全部行 key（rowMap 的插入顺序在 replaces 后会变） */
    D.rowKeys = Array.from(el.list.querySelectorAll('.row:not([hidden])')).map(function (node) { return node.dataset.key; });
    D.allRowKeys = Array.from(el.list.querySelectorAll('.row')).map(function (node) { return node.dataset.key; });
    D.deviceKeys = Array.from(state.devices.keys());
    D.heroKey = state.heroKey;
    D.heroRowKey = state.heroKey;
    D.heroPinnedByUser = state.heroPinnedByUser;
    D.pinnedHeroKey = state.pinnedHeroKey;
    D.session = state.session;
    D.seq = state.seq;
    D.trayPrimed = trayPrimed;
    D.settings = { lowThreshold: settings.lowThreshold, reducedMotion: settings.reducedMotion, pinned: state.pinned };
    D.effects = root.dataset.effects || 'none';
    D.acrylic = root.dataset.acrylic || '';
    D.reducedMotion = prefersReducedMotion();
    D.hidden = state.hidden;
    D.introActive = document.body.classList.contains('intro');
    D.lastRenderAt = Date.now();
    D.reason = reason || '';
    state.lastUpdate = D.lastRenderAt;
    state.dirty = false;
  }

  /* ========================================================== 协议事件 */

  function normalizeDevice(raw) {
    if (!raw || typeof raw !== 'object' || typeof raw.key !== 'string' || !raw.key) return null;
    return {
      key: raw.key,
      name: typeof raw.name === 'string' && raw.name ? raw.name : raw.key,
      level: raw.level == null ? null : clamp(Math.round(Number(raw.level)), 0, 100),
      approx: typeof raw.approx === 'string' ? raw.approx : '',
      left_s: raw.left_s == null ? null : (function () {
      const n = Number(raw.left_s);
      return Number.isFinite(n) ? Math.max(0, Math.round(n)) : null;
    })(),
      charging: !!raw.charging,
      online: raw.online !== false,
      kind: typeof raw.kind === 'string' && raw.kind ? raw.kind : 'bluetooth',
      transport: typeof raw.transport === 'string' && raw.transport ? raw.transport : 'unknown',
      via: typeof raw.via === 'string' ? raw.via : '',
      updated_unix: Number(raw.updated_unix) || 0
    };
  }

  function upsertDevice(raw, replaces) {
    const d = normalizeDevice(raw);
    if (!d) return null;
    const rep = typeof replaces === 'string' && replaces && replaces !== d.key ? replaces : '';
    if (rep) {
      state.devices.delete(rep);
      const oi = state.order.indexOf(rep);
      /* P3-14：目标 key 若已存在于 order（同型号 key 迁移），先清掉旧位置，
         避免留下幽灵顺序项；新 key 占用被替换 key 的位置。 */
      const rest = state.order.filter(function (k) { return k !== rep && k !== d.key; });
      if (oi >= 0) {
        let insertAt = 0;
        for (let i = 0; i < oi && i < state.order.length; i++) {
          if (state.order[i] !== rep && state.order[i] !== d.key) insertAt++;
        }
        rest.splice(insertAt, 0, d.key);
      } else if (state.order.indexOf(d.key) < 0) {
        rest.push(d.key);
      }
      state.order = rest;
      if (state.heroKey === rep) state.heroKey = d.key;
      if (state.pinnedHeroKey === rep) state.pinnedHeroKey = d.key;
      /* key 迁移 → 行 DOM 原地 morph（保留节点/位置/动画） */
      const rec = rowMap.get(rep);
      if (rec) {
        rowMap.delete(rep);
        rec.el.dataset.key = d.key;
        rowMap.set(d.key, rec);
      }
      state.morphFrom = rep;
    } else if (state.order.indexOf(d.key) < 0) {
      state.order.push(d.key);
    }
    state.devices.set(d.key, d);
    return d;
  }

  function removeDevice(key) {
    if (!key) return;
    state.devices.delete(key);
    const oi = state.order.indexOf(key);
    if (oi >= 0) state.order.splice(oi, 1);
    if (state.pinnedHeroKey === key) {
      state.pinnedHeroKey = null;
      state.heroPinnedByUser = false;
    }
    if (state.heroKey === key) state.heroKey = null;
  }

  function resetSession(hello) {
    D.sessions++;
    state.session = hello && hello.session ? hello.session : state.session;
    state.seq = 0;
    state.devices = new Map();
    state.order = [];
    state.heroKey = null;
    state.heroPinnedByUser = false;
    state.pinnedHeroKey = null;
    state.morphFrom = null;
    state.versionError = false;
    if (hello) {
      if (typeof hello.build === 'string' && hello.build) state.build = hello.build;
      if (hello.protocol != null) state.protocol = hello.protocol;
      state.collectorState = state.collectorState && (state.collectorState.state === 'error' || state.collectorState.state === 'exited') ? state.collectorState : { state: 'ready' };
      (hello.devices || []).forEach(function (raw) { upsertDevice(raw, ''); });
    }
    lastHero.key = null;
    lastHero.level = null;
    lastTrayJson = '';
    /* 注意：不重播入场动画（body.intro 保持原状） */
    render('session');
  }

  function handleCollectorMessage(raw) {
    D.events++;
    let ev = raw;
    if (typeof raw === 'string') {
      try { ev = JSON.parse(raw); } catch (e) { D.badEvents++; return; }
    }
    if (!ev || typeof ev !== 'object' || typeof ev.t !== 'string') { D.badEvents++; return; }

    if (ev.t === 'hello') {
      if (ev.protocol != null && ev.protocol !== 1) {
        state.versionError = true;
        state.protocol = ev.protocol;
        state.collectorState = { state: 'error', message: '采集器版本过期' };
        render('version-error');
        return;
      }
      if (state.session && ev.session === state.session) return; /* 重复 hello */
      state.helloReceived = true;
      resetSession(ev);
      return;
    }

    if (ev.session && state.session && ev.session !== state.session) {
      /* 新 session 但 hello 还没到：先全量重置再处理该事件 */
      resetSession({ session: ev.session, build: ev.build, protocol: ev.protocol, devices: [] });
    } else if (ev.session && !state.session) {
      state.session = ev.session;
    }

    if (typeof ev.seq === 'number') {
      if (ev.seq <= state.seq) { D.dropped++; return; }
      state.seq = ev.seq;
    }

    switch (ev.t) {
      case 'device':
        upsertDevice(ev.device, ev.replaces);
        break;
      case 'gone':
        removeDevice(ev.key);
        break;
      case 'tick': {
        const incoming = Array.isArray(ev.devices) ? ev.devices : [];
        const keys = new Set();
        incoming.forEach(function (raw) {
          const d = upsertDevice(raw, '');
          if (d) keys.add(d.key);
        });
        Array.from(state.devices.keys()).forEach(function (key) {
          if (!keys.has(key)) removeDevice(key);
        });
        break;
      }
      case 'alert':
        handleAlert(ev);
        break;
      case 'stat':
        D.stat = { providers: ev.providers || {}, timers_per_s: ev.timers_per_s, key_migrations: ev.key_migrations };
        break;
      default:
        break;
    }
    render(ev.t);
  }

  function handleCollectorState(cs) {
    if (!cs || typeof cs !== 'object') return;
    state.collectorState = cs;
    if (cs.protocol != null && cs.protocol !== 1) state.versionError = true;
    if (typeof cs.build === 'string' && cs.build) state.build = cs.build;
    bridgeDebug.collectorState = cs;
    /* hello 可能在 WebView 就绪前就被 Rust 转发掉了：ready 但从未收到 hello 时，
       主动发一条 get snapshot 让采集器立刻回 tick（协议 §2），避免空白等待 30s。 */
    if (cs.state === 'ready' && !state.helloReceived) {
      const now = Date.now();
      if (now - state.lastSnapshotRequest > 1500) {
        state.lastSnapshotRequest = now;
        bridge.call('collector_send', { line: JSON.stringify({ t: 'get', what: 'snapshot' }) });
      }
    }
    if (state.hidden) { state.dirty = true; return; }
    render('collector-state');
  }

  function handleAppState(as) {
    if (!as || typeof as !== 'object') return;
    /* A14 / P2-46 / §10.3：Rust 会在窗口显示前也 emit app-state（此时前端可能仍是
       隐藏态），acrylic 不可用时先落 html[data-acrylic="off"]，保证窗口第一次可见
       就是实底降级的正确样式（styles.css 里 .panel 的实底降级规则）。
       只写一个属性、不触发布局，不违反隐藏态「不写 DOM」的性能约束。 */
    if (typeof as.acrylic === 'boolean') {
      root.dataset.acrylic = as.acrylic === false ? 'off' : 'on';
      D.acrylic = root.dataset.acrylic;
    }
    /* A12：Tauri 下托盘「固定面板」由 Rust 直接 set_pinned 并 emit app-state，
       app-state 是唯一事实源；这里只同步状态/UI，绝不能再 invoke set_pinned
       （否则与本地 setPinned 形成 set_pinned ↔ app-state 反馈环）。 */
    const pinnedChanged = typeof as.pinned === 'boolean';
    if (pinnedChanged) {
      state.pinned = as.pinned;
      settings.pinned = as.pinned;
      saveSettings();
    }
    if (as.platform && typeof as.platform.windows11 === 'boolean') {
      state.platform = {
        windows11: as.platform.windows11,
        build: Number(as.platform.build) || 0
      };
      root.dataset.windows11 = String(state.platform.windows11);
    }
    if (typeof as.build === 'string' && as.build) state.build = as.build;
    if (typeof as.version === 'string' && as.version) state.appVersion = as.version;
    if (state.hidden) {
      state.dirty = true;
      /* A12：隐藏态 WebView 可能已暂停；pin 状态先记在 D.pendingPin，
         恢复显示后由 render()/updatePinUI() 统一回写。 */
      if (pinnedChanged) D.pendingPin = state.pinned;
      return;
    }
    updateInfo();
    updatePinUI();
  }

  /* ========================================================== 告警 toast */

  const pendingAlerts = [];

  function defaultAlertText(ev) {
    const name = ev.key ? deviceName(ev.key) : '设备';
    if (ev.kind === 'full') return name + ' 已充满';
    if (ev.kind === 'offline') return name + ' 已离线';
    return name + (ev.level != null ? ' 电量仅剩 ' + ev.level + '%' : ' 电量低') + '，建议充电';
  }

  function handleAlert(ev) {
    const kind = ev.kind === 'full' ? 'full' : (ev.kind === 'offline' ? 'offline' : 'low');
    const text = (typeof ev.text === 'string' && ev.text) ? ev.text : defaultAlertText(ev);
    D.lastAlert = { kind: kind, key: ev.key || '', level: ev.level == null ? null : ev.level, text: text };
    if (state.hidden) {
      pendingAlerts.push({ kind: kind, text: text });
      if (pendingAlerts.length > 3) pendingAlerts.shift();
      return;
    }
    showToast(kind, text);
  }

  const toastTimers = new Map();

  function showToast(kind, text) {
    markHiddenWrite('showToast');
    D.toasts++;
    const t = document.createElement('div');
    t.className = 'toast is-' + kind;
    const dot = document.createElement('span');
    dot.className = 'toast-dot';
    const txt = document.createElement('span');
    txt.className = 'toast-text';
    txt.textContent = text;
    t.appendChild(dot);
    t.appendChild(txt);
    t.addEventListener('click', function () { dismissToast(t); });
    el.toasts.appendChild(t);
    while (el.toasts.children.length > 2) {
      const first = el.toasts.firstChild;
      dismissToast(first);
      if (first && first.parentNode) first.parentNode.removeChild(first);
    }
    scheduleToastDismiss(t, TOAST_MS);
  }

  function scheduleToastDismiss(t, ms) {
    const prev = toastTimers.get(t);
    if (prev) clearT(prev);
    const id = setT(function () { toastTimers.delete(t); dismissToast(t); }, ms);
    toastTimers.set(t, id);
  }

  function dismissToast(t) {
    if (!t || !t.parentNode) return;
    const id = toastTimers.get(t);
    if (id) { clearT(id); toastTimers.delete(t); }
    t.remove();
  }

  function flushPendingAlerts() {
    while (pendingAlerts.length) {
      const a = pendingAlerts.shift();
      showToast(a.kind, a.text);
    }
  }

  /* ========================================================== 隐藏四步（前端部分） */

  let pausedAnimations = [];

  function enterHidden() {
    D.hidden = true;
    D.hiddenEvents++;
    document.body.classList.add('is-hidden');
    /* 2. 停 rAF + 清 setTimeout */
    clearAllTimers();
    cancelAllRaf();
    el['glow-layer'].classList.remove('is-tracking');
    clearSpotlight();
    /* 1. 暂停全部 Web Animations */
    pausedAnimations = [];
    try {
      document.getAnimations().forEach(function (a) {
        try { a.pause(); } catch (e) { /* ignore */ }
        pausedAnimations.push(a);
      });
    } catch (e) { /* ignore */ }
    D.pausedAnimations = pausedAnimations.length;
    /* 3. 隐藏期间不写 DOM：render() 只在 state.dirty 标记 */
    state.dirty = state.dirty || true;
  }

  function exitHidden() {
    D.hidden = false;
    document.body.classList.remove('is-hidden');
    /* 隐藏期间若中断了入场，恢复时直接收尾，不重播 */
    if (document.body.classList.contains('intro')) document.body.classList.remove('intro');
    /* 4. 先用最后快照渲染一帧（此时 hidden 已为 false） */
    render('resume');
    /* 再恢复动画 */
    pausedAnimations.forEach(function (a) {
      try { a.play(); } catch (e) { /* ignore */ }
    });
    pausedAnimations = [];
    D.pausedAnimations = 0;
    /* 隐藏期间挂起的告警与 toast 计时 */
    flushPendingAlerts();
    Array.from(el.toasts.children).forEach(function (t) { scheduleToastDismiss(t, 2600); });
    /* 隐藏期间收到的托盘动作（设置/刷新）在恢复后补做 */
    applyPendingUi();
    D.introActive = document.body.classList.contains('intro');
  }

  function applyPendingUi() {
    if (pendingUi.settings) {
      pendingUi.settings = false;
      D.pendingSettings = false;
      setDrawer(true);
    }
    if (pendingUi.spin) {
      pendingUi.spin = false;
      D.pendingSpin = false;
      spinRefresh();
    }
  }

  function handleVisibility(payload) {
    const hidden = !!(payload && payload.hidden);
    if (hidden === state.hidden) return;
    state.hidden = hidden;
    if (hidden) enterHidden();
    else exitHidden();
  }

  /* ========================================================== 托盘动作 / 交互 */

  function selectHero(key, viaKeyboard) {
    if (!state.devices.has(key)) return;
    state.pinnedHeroKey = key;
    state.heroPinnedByUser = true;
    render('select-hero');
    if (viaKeyboard) {
      el.hero.setAttribute('tabindex', '-1');
      el.hero.focus();
    }
  }

  function setPinned(pinned, viaUi) {
    state.pinned = !!pinned;
    settings.pinned = state.pinned;
    saveSettings();
    bridge.call('set_pinned', { pinned: state.pinned });
    if (state.hidden) {
      /* P2-3：隐藏态只记状态 + invoke，不写 DOM；恢复时 render() 统一回写 */
      state.dirty = true;
      D.pendingPin = state.pinned;
      D.hiddenActionsBlocked++;
      return;
    }
    updatePinUI();
    if (viaUi) D.lastPinUiAt = Date.now();
  }

  function togglePin() { setPinned(!state.pinned, true); }

  function spinRefresh() {
    if (state.hidden) {
      /* P2-3：隐藏时只排队，不创建 WAAPI */

      pendingUi.spin = true;
      state.dirty = true;
      D.pendingSpin = true;
      D.hiddenActionsBlocked++;
      return;
    }
    if (prefersReducedMotion()) return;
    const btn = el['btn-refresh'];
    if (btn && btn.animate) {
      try {
        btn.animate(
          [{ transform: 'rotate(0deg)' }, { transform: 'rotate(360deg)' }],
          { duration: 600, easing: 'cubic-bezier(.22,.61,.36,1)' }
        );
      } catch (e) { /* ignore */ }
    }
  }

  function doRefresh() {
    bridge.call('collector_send', { line: JSON.stringify({ t: 'refresh' }) });
    spinRefresh();
  }

  function restartCollector() {
    state.collectorState = { state: 'starting' };
    state.versionError = false;
    render('restart');
    bridge.call('collector_restart');
  }

  function windowAction(action) {
    bridge.call('window_action', { action: action });
  }

  function handleTrayAction(payload) {
    const action = payload && payload.action;
    if (action === 'refresh') {
      /* Tauri 托盘的 refresh 已由 Rust 自己触发采集器刷新，这里只做视觉反馈 */
      if (bridge.hasTauri) spinRefresh();
      else doRefresh();
    } else if (action === 'settings') openDrawer();
    else if (action === 'toggle-pin') {
      /* A12：Tauri 下托盘「固定面板」由 Rust 直接 set_pinned 并 emit app-state
         （隐藏态 WebView 暂停也不丢事件），前端不再自行 toggle，避免 Rust 已翻转后
         前端再翻转回去或形成 set_pinned ↔ app-state 反馈环。
         浏览器 mock/预览没有 Rust 托盘，保留本地 toggle 以覆盖隐藏态守卫。 */
      if (!bridge.hasTauri) togglePin();
    } else if (action === 'quit') {
      /* Rust 托盘 quit 已经走退出流程，前端不再重复请求 */
      if (!bridge.hasTauri) windowAction('quit');
    }
  }

  /* ========================================================== 涟漪 / 柔光 */

  let specularPending = null;
  let specularRaf = 0;

  /* 行/主卡悬停柔光：同一时刻只点亮一个元素，rAF 合并，隐藏/降低动效时清除。 */
  let litTarget = null;
  let litPending = null;
  let litRaf = 0;

  function clearSpotlight() {
    if (litTarget) {
      litTarget.style.removeProperty('--mx');
      litTarget.style.removeProperty('--my');
      litTarget = null;
    }
    litPending = null;
    litRaf = 0;
  }

  function flushSpecular() {
    specularRaf = 0;
    if (state.hidden || !specularPending) return;
    const r = el.panel.getBoundingClientRect();
    const x = specularPending.x - r.left;
    const y = specularPending.y - r.top;
    specularPending = null;
    el.specular.style.transform = 'translate3d(' + x.toFixed(1) + 'px,' + y.toFixed(1) + 'px,0)';
  }

  function flushSpotlight() {
    litRaf = 0;
    if (state.hidden || !litTarget || !litPending) return;
    const r = litTarget.getBoundingClientRect();
    litTarget.style.setProperty('--mx', (litPending.x - r.left).toFixed(1) + 'px');
    litTarget.style.setProperty('--my', (litPending.y - r.top).toFixed(1) + 'px');
    litPending = null;
  }

  function updateSpotlight(e) {
    const target = e.target && e.target.closest
      ? e.target.closest('.row, .hero-card')
      : null;
    if (target !== litTarget) {
      if (litTarget) {
        litTarget.style.removeProperty('--mx');
        litTarget.style.removeProperty('--my');
      }
      litTarget = target;
    }
    if (!litTarget) return;
    litPending = { x: e.clientX, y: e.clientY };
    if (!litRaf) litRaf = raf(flushSpotlight);
  }

  function onPointerMove(e) {
    if (state.hidden || prefersReducedMotion()) return;
    specularPending = { x: e.clientX, y: e.clientY };
    if (!specularRaf) specularRaf = raf(flushSpecular);
    updateSpotlight(e);
  }

  function spawnRipple(target, clientX, clientY, small) {
    if (state.hidden || prefersReducedMotion()) return;
    const layer = el['ripple-layer'];
    const lr = layer.getBoundingClientRect();
    const ripple = document.createElement('span');
    ripple.className = 'ripple' + (small ? ' ripple-sm' : '');
    ripple.style.setProperty('--rx', (clientX - lr.left).toFixed(1) + 'px');
    ripple.style.setProperty('--ry', (clientY - lr.top).toFixed(1) + 'px');
    ripple.addEventListener('animationend', function () { ripple.remove(); });
    layer.appendChild(ripple);
    setT(function () { if (ripple.parentNode) ripple.remove(); }, 760);
  }

  /* ========================================================== 设置抽屉 */

  let drawerOpen = false;
  let focusBeforeDrawer = null;

  function setDrawer(open) {
    if (state.hidden) {
      /* P2-3：隐藏时托盘「设置」只记意图；窗口显示后（exitHidden）再真正打开抽屉 */
      pendingUi.settings = !!open;
      state.dirty = true;
      D.pendingSettings = pendingUi.settings;
      D.hiddenActionsBlocked++;
      return;
    }
    drawerOpen = !!open;
    el.drawer.classList.toggle('is-open', drawerOpen);
    el.drawer.setAttribute('aria-hidden', String(!drawerOpen));
    el['btn-settings'].setAttribute('aria-expanded', String(drawerOpen));
    el['drawer-scrim'].hidden = !drawerOpen;
    if (drawerOpen) {
      el.drawer.removeAttribute('inert');
      focusBeforeDrawer = document.activeElement;
      if (el['set-threshold']) {
        try { el['set-threshold'].focus(); } catch (e) { /* ignore */ }
      }
    } else {
      el.drawer.setAttribute('inert', '');
      if (focusBeforeDrawer && focusBeforeDrawer.focus) {
        try { focusBeforeDrawer.focus(); } catch (e) { /* ignore */ }
      }
      focusBeforeDrawer = null;
    }
  }

  function openDrawer() { setDrawer(true); }
  function closeDrawer() { setDrawer(false); }

  function onDrawerKeydown(e) {
    if (!drawerOpen) return;
    if (e.key === 'Escape') { e.preventDefault(); closeDrawer(); return; }
    if (e.key !== 'Tab') return;
    const focusables = el.drawer.querySelectorAll('button, input, [tabindex]:not([tabindex="-1"])');
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  function applyReducedMotion() {
    if (prefersReducedMotion()) {
      root.dataset.reducedMotion = 'true';
      D.reducedMotion = true;
      el['glow-layer'].classList.remove('is-tracking');
      clearSpotlight();
      cancelHeroTween();
    } else {
      delete root.dataset.reducedMotion;
      D.reducedMotion = false;
    }
    el['set-reduced'].checked = !!settings.reducedMotion;
  }

  /* ========================================================== 生产面板系统交互拦截 */

  /* P1-09：wry 默认 browser_accelerator_keys/default_context_menus 会让生产面板弹出
     WebView2 浏览器右键菜单；F5/Ctrl+R 会整页重载（产生第二次 ui_ready/show，把用户
     拖到别处的窗口重置回托盘锚点，并丢失主卡/抽屉状态），Ctrl+P 打印预览覆盖透明小窗。
     这里在 document 捕获阶段兜底拦截（wry 层关闭默认菜单后仍有效，浏览器评审也不误触）。
     只拦截明确列出的浏览器级快捷键，Ctrl+C/V/X/A/Z 等文本编辑键不受影响。 */
  function isBlockedAccelerator(e) {
    if (e.altKey) return false;
    const key = String(e.key || '').toLowerCase();
    if (key === 'f5') return true;
    if (!e.ctrlKey) return false;
    return key === 'r' || key === 'p' || key === 'f' || key === 's' || key === 'u';
  }

  function onBlockedAccelerator(e) {
    if (!isBlockedAccelerator(e)) return;
    e.preventDefault();
    e.stopPropagation();
    D.blockedShortcuts++;
    D.lastBlockedShortcut = String(e.key || '');
  }

  function onBlockedContextMenu(e) {
    /* 生产（Tauri）与浏览器 mock 都生效：面板不出现 WebView2 默认右键菜单 */
    e.preventDefault();
    e.stopPropagation();
    D.blockedContextMenus++;
  }

  /* ========================================================== 事件绑定 */

  function bindEvents() {
    /* 协议事件 */
    bridge.on('collector-message', handleCollectorMessage);
    bridge.on('collector-state', handleCollectorState);
    bridge.on('app-state', handleAppState);
    bridge.on('tray-action', handleTrayAction);
    bridge.on('window-visibility', handleVisibility);

    /* 按钮 */
    el['btn-refresh'].addEventListener('click', doRefresh);
    el['btn-pin'].addEventListener('click', togglePin);
    el['btn-settings'].addEventListener('click', function () { setDrawer(!drawerOpen); });
    el['btn-min'].addEventListener('click', function () { windowAction('minimize'); });
    el['btn-close'].addEventListener('click', function () { windowAction('hide'); });
    el['btn-restart'].addEventListener('click', restartCollector);
    el['btn-all'].addEventListener('click', function () {
      const first = el.list.querySelector('.row');
      if (first) first.focus();
      if (!state.devices.size) openDrawer();
    });
    el['btn-drawer-close'].addEventListener('click', closeDrawer);
    el['drawer-scrim'].addEventListener('click', closeDrawer);
    el.drawer.addEventListener('keydown', onDrawerKeydown);

    /* 设置项 */
    el['set-threshold'].addEventListener('input', function () {
      settings.lowThreshold = clamp(Math.round(Number(el['set-threshold'].value) || 15), 5, 50);
      el['set-threshold-out'].textContent = settings.lowThreshold + '%';
      D.lowThreshold = settings.lowThreshold;
      saveSettings();
      if (!state.hidden) render('threshold');
    });
    el['set-reduced'].addEventListener('change', function () {
      settings.reducedMotion = el['set-reduced'].checked;
      saveSettings();
      applyReducedMotion();
    });
    el['set-pinned'].addEventListener('change', function () { setPinned(el['set-pinned'].checked, true); });

    /* 指针柔光（rAF 合并 pointermove；will-change 只在进入期间） */
    el.panel.addEventListener('pointermove', onPointerMove, { passive: true });
    el.panel.addEventListener('pointerenter', function () {
      if (!state.hidden && !prefersReducedMotion()) el['glow-layer'].classList.add('is-tracking');
    });
    el.panel.addEventListener('pointerleave', function () {
      el['glow-layer'].classList.remove('is-tracking');
      clearSpotlight();
    });

    /* 点击涟漪：按钮/行一套小涟漪，空白玻璃一套大涟漪 */
    el.panel.addEventListener('pointerdown', function (e) {
      if (state.hidden || prefersReducedMotion()) return;
      if (drawerOpen && el.drawer.contains(e.target)) return;
      const target = e.target.closest ? e.target.closest('.row, .ctrl, .all-btn, .banner-btn') : null;
      if (target) spawnRipple(target, e.clientX, e.clientY, true);
      else if (!el.drawer.contains(e.target)) spawnRipple(el.panel, e.clientX, e.clientY, false);
    });

    /* 全局 Esc 关闭抽屉 */
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && drawerOpen) { e.preventDefault(); closeDrawer(); }
    });

    /* P1-09：浏览器右键菜单 / 加速键兜底拦截（捕获阶段先于页面其它处理） */
    document.addEventListener('contextmenu', onBlockedContextMenu, true);
    document.addEventListener('keydown', onBlockedAccelerator, true);
  }

  /* ========================================================== 启动 */

  function boot() {
    cacheDom();
    el['set-threshold'].value = String(settings.lowThreshold);
    el['set-threshold-out'].textContent = settings.lowThreshold + '%';
    applyReducedMotion();
    updatePinUI();
    updateInfo();
    bindEvents();

    /* 入场只播一次；session 变化不会重播 */
    document.body.classList.add('intro');
    D.introPlays++;
    D.introActive = true;
    setT(function () {
      document.body.classList.remove('intro');
      D.introActive = false;
      D.introPlaysFinal = D.introPlays;
    }, INTRO_MS);

    render('boot');

    bridge.start().then(function () {
      render('ready');
      return bridge.ready();
    }).catch(function () {
      render('start-failed');
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
