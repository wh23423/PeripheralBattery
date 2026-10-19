/* ============================================================
   潮息 · Tidal Breath — 演示页脚本
   只驱动「画廊」与状态切换；面板里所有运动都由 tokens.css 的
   时长 / 缓动 / 关键帧负责。这里没有第二套时间常量。
   ============================================================ */
(function () {
  'use strict';

  const $ = function (sel, root) { return (root || document).querySelector(sel); };
  const RING_C = 282.7433;
  const html = document.documentElement;

  const el = {
    panel: $('#panel'),
    stage: $('#stage'),
    list: $('#list'),
    heroRing: $('#hero-ring'),
    heroLevel: $('#hero-level'),
    heroNum: $('#hero-num'),
    heroPct: $('#hero-pct'),
    heroCaption: $('#hero-caption'),
    heroName: $('#hero-name'),
    heroIcon: $('#hero-icon'),
    heroTransport: $('#hero-transport'),
    heroAlert: $('#hero-alert'),
    heroCard: $('.hero-card'),
    heroTitle: $('.hero-title'),
    heroSub: $('.hero-sub'),
    live: $('#live'),
    liveText: $('#live-text'),
    subtitle: $('#subtitle'),
    banner: $('#banner'),
    toasts: $('#toasts'),
    empty: $('#empty'),
    drawer: $('#drawer'),
    scrim: $('#drawer-scrim'),
    drawerClose: $('#btn-drawer-close'),
    settingsBtn: $('#btn-settings'),
    refreshBtn: $('#btn-refresh'),
    pinBtn: $('#btn-pin'),
    allBtn: $('#btn-all'),
    rippleLayer: $('#ripple-layer'),
    specular: $('#specular'),
    glowLayer: $('#glow-layer'),
    rail: $('#rail'),
    insTitle: $('#ins-title'),
    insNote: $('#ins-note'),
    timeline: $('#timeline'),
    insTokens: $('#ins-tokens'),
    clockGrid: $('#clock-grid'),
    durTable: $('#dur-table'),
    easeTable: $('#ease-table'),
    optReduced: $('#opt-reduced'),
    optForce: $('#opt-force'),
    optSlow: $('#opt-slow'),
    optPause: $('#opt-pause'),
    tourBtn: $('#btn-tour'),
    motionAlert: $('#motion-alert'),
    motionAlertBody: $('#motion-alert-body'),
    forceBtn: $('#btn-force-motion')
  };

  const redMQ = window.matchMedia('(prefers-reduced-motion: reduce)');

  function forced() { return html.hasAttribute('data-force-motion'); }

  // 优先级：手动「降低动效」永远有效；「强制播放」只覆盖系统级 reduce 偏好。
  function isReduced() {
    if (html.getAttribute('data-reduced-motion') === 'true') return true;
    if (forced()) return false;
    return redMQ.matches;
  }
  function speed() {
    const v = parseFloat(getComputedStyle(html).getPropertyValue('--td-speed'));
    return isFinite(v) && v > 0 ? v : 1;
  }
  function wait(ms) { return new Promise(function (r) { setTimeout(r, ms * speed()); }); }
  function token() { return runToken; }

  /* ---------------------------------------------------------- 图标 */
  const ICONS = {
    headset: '<path d="M5 15.2v-3.4a7 7 0 0 1 14 0v3.4"/><rect x="3" y="14" width="4.8" height="6.6" rx="2.4"/><rect x="16.2" y="14" width="4.8" height="6.6" rx="2.4"/>',
    mouse: '<rect x="6.2" y="2.4" width="11.6" height="19.2" rx="5.8"/><path d="M12 6.6v3.8"/>',
    keyboard: '<rect x="2" y="6" width="20" height="12" rx="2.8"/><path d="M6.2 10h.01M9.9 10h.01M13.6 10h.01M17.3 10h.01M8 14.2h8"/>',
    gamepad: '<path d="M7 8h10a4 4 0 0 1 3.9 4.9l-.8 3.5a2.4 2.4 0 0 1-4.2 1L14 15h-4l-1.9 2.4a2.4 2.4 0 0 1-4.2-1l-.8-3.5A4 4 0 0 1 7 8Z"/><path d="M8 11v2M7 12h2"/><circle cx="15.6" cy="11.6" r=".9"/><circle cx="17.4" cy="13.4" r=".9"/>'
  };

  function icon(kind, cls) {
    return '<svg class="' + cls + '" viewBox="0 0 24 24" aria-hidden="true">' +
      (ICONS[kind] || ICONS.mouse) + '</svg>';
  }

  /* ---------------------------------------------------------- 设备数据 */
  function mkDevice(o) {
    return {
      key: o.key,
      name: o.name,
      kind: o.kind,
      level: o.level,
      transport: o.transport,
      left: o.left,
      caption: o.caption,
      charging: !!o.charging,
      offline: !!o.offline
    };
  }

  const DEVICES = [
    mkDevice({ key: 'xbox', name: 'Xbox Elite 2', kind: 'gamepad', level: 45, transport: '蓝牙', left: '充电中', caption: '充电中', charging: true }),
    mkDevice({ key: 'keychron', name: 'Keychron Q1 Pro', kind: 'keyboard', level: 92, transport: '蓝牙', left: '约 3 天', caption: '约 3 天' }),
    mkDevice({ key: 'arctis', name: 'Arctis Nova 7', kind: 'headset', level: 71, transport: '2.4 GHz', left: '约 12 小时', caption: '约 12 小时' }),
    mkDevice({ key: 'viper', name: 'Viper V3 Pro', kind: 'mouse', level: 68, transport: '2.4 GHz', left: '约 18 小时', caption: '约 18 小时' })
  ];

  const BASE_DEVICES = DEVICES.map(function (d) { return Object.assign({}, d); });

  const BASE_HERO = 'viper';
  const BASE_LIVE = { cls: '', text: '在线' };
  let liveCls = '';
  let runToken = 0;
  let shownLevel = 68;
  let heroKey = BASE_HERO;
  let showAll = false;

  function device(key) {
    for (let i = 0; i < DEVICES.length; i++) if (DEVICES[i].key === key) return DEVICES[i];
    return null;
  }

  function stateOf(d) {
    if (d.offline) return 'offline';
    if (d.charging) return 'charging';
    if (d.level == null) return 'unknown';
    if (d.level <= 15) return 'low';
    if (d.level <= 35) return 'amber';
    return 'normal';
  }

  function levelText(level) {
    return level == null ? '未知' : level + '%';
  }

  /* ---------------------------------------------------------- 主环 */
  const STATES = ['normal', 'low', 'amber', 'charging', 'offline', 'unknown', 'empty'];

  function setRingState(state) {
    STATES.forEach(function (s) { el.heroRing.classList.remove('state-' + s); });
    el.heroRing.classList.add('state-' + state);
  }

  function paintRing(level, animate) {
    const fill = el.heroRing.querySelector('.fill');
    const meniscus = el.heroRing.querySelector('.meniscus');
    const off = level == null ? RING_C : RING_C * (1 - level / 100);
    fill.style.transition = animate === false ? 'none' : '';
    fill.style.strokeDashoffset = off;
    if (meniscus) {
      meniscus.style.transition = animate === false ? 'none' : '';
      meniscus.style.transform = level == null ? 'rotate(0deg)' : 'rotate(' + (level * 3.6) + 'deg)';
    }
    if (animate === false) {
      void fill.getBoundingClientRect();
      fill.style.transition = '';
    }
  }

  function tweenNumber(from, to, dur) {
    const id = ++heroTweenToken;
    if (isReduced() || dur <= 0) {
      shownLevel = to;
      el.heroNum.textContent = String(Math.round(to));
      return Promise.resolve();
    }
    const t0 = performance.now();
    const total = dur * speed();
    return new Promise(function (resolve) {
      function tick(now) {
        if (id !== heroTweenToken) { resolve(); return; }
        const k = Math.min(1, (now - t0) / total);
        const e = 1 - Math.pow(1 - k, 3);
        const v = from + (to - from) * e;
        shownLevel = v;
        el.heroNum.textContent = String(Math.round(v));
        if (k < 1) requestAnimationFrame(tick);
        else resolve();
      }
      requestAnimationFrame(tick);
    });
  }
  let heroTweenToken = 0;

  function heroContent(d, st) {
    el.heroName.textContent = d.name;
    el.heroIcon.innerHTML = icon(d.kind, 'kind-ico');
    el.heroTransport.textContent = d.transport || '—';
    el.heroAlert.hidden = !(st === 'low');
    el.heroCaption.textContent = d.caption || '剩余时间未知';
    el.heroLevel.classList.toggle('is-text', d.level == null);
    el.heroPct.hidden = d.level == null;
    if (d.level == null) el.heroNum.textContent = '--';
  }

  function animateMeta(framesOut, framesIn, durOut, durIn) {
    const targets = [el.heroTitle, el.heroSub, el.heroCaption];
    if (isReduced()) return Promise.resolve();
    const outs = targets.map(function (t) {
      return t.animate(framesOut, { duration: durOut * speed(), easing: 'cubic-bezier(0.5, 0, 0.9, 0.4)', fill: 'forwards' }).finished.catch(function () {});
    });
    return Promise.all(outs).then(function () {
      targets.forEach(function (t) { t.getAnimations().forEach(function (a) { a.cancel(); }); });
      const ins = targets.map(function (t) {
        return t.animate(framesIn, { duration: durIn * speed(), easing: 'cubic-bezier(0.16, 1, 0.3, 1)', fill: 'forwards' }).finished.catch(function () {});
      });
      return Promise.all(ins).then(function () {
        targets.forEach(function (t) { t.getAnimations().forEach(function (a) { a.cancel(); }); });
      });
    });
  }

  /* 主卡交接：容器先承接（环色/液面/tint），内容再交叉。 */
  async function applyHero(d, opts) {
    opts = opts || {};
    const crossfade = opts.crossfade !== false;
    const st = stateOf(d);
    const nextLevel = d.level;
    heroKey = d.key;

    setRingState(st);
    paintRing(nextLevel, true);

    const from = shownLevel;
    const tweenP = (nextLevel == null)
      ? Promise.resolve()
      : tweenNumber(from, nextLevel, 620);

    if (d.level == null) { shownLevel = 0; }

    if (crossfade && !isReduced()) {
      await animateMeta(
        [{ opacity: 1, transform: 'translateY(0)' }, { opacity: 0, transform: 'translateY(-4px)' }],
        [{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'translateY(0)' }],
        160, 320
      );
    }
    heroContent(d, st);
    await tweenP;
  }

  /* ---------------------------------------------------------- 列表 */
  function rowNode(d) {
    const st = stateOf(d);
    const row = document.createElement('div');
    row.className = 'row' + (d.offline ? ' is-offline' : '');
    row.setAttribute('role', 'button');
    row.tabIndex = 0;
    row.dataset.key = d.key;
    row.innerHTML =
      '<div class="ring ring-sm state-' + st + '">' +
        '<svg viewBox="0 0 100 100" aria-hidden="true">' +
          '<circle class="track" cx="50" cy="50" r="45"/>' +
          '<circle class="glow" cx="50" cy="50" r="45"/>' +
          '<circle class="fill" cx="50" cy="50" r="45"/>' +
          '<circle class="meniscus" cx="50" cy="50" r="45"/>' +
        '</svg>' +
        '<div class="ring-center"><span class="ring-glyph-wrap">' + icon(d.kind, 'ring-glyph') + '</span></div>' +
      '</div>' +
      '<div class="row-main">' +
        '<div class="row-name">' + d.name + '</div>' +
        '<div class="row-sub">' +
          '<span class="row-kind">' + icon(d.kind, 'kind-ico') + '</span>' +
          '<span class="row-transport">' + (d.transport || '') + '</span>' +
          '<span class="dot">·</span>' +
          '<span class="row-left">' + (d.left || '') + '</span>' +
        '</div>' +
      '</div>' +
      '<div class="row-right"><span class="row-level">' + levelText(d.level) + '</span>' +
        '<svg class="caret" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 18l6-6-6-6"/></svg></div>';

    const fill = row.querySelector('.fill');
    const meniscus = row.querySelector('.meniscus');
    fill.style.strokeDashoffset = d.level == null ? RING_C : RING_C * (1 - d.level / 100);
    if (meniscus && d.level != null) meniscus.style.transform = 'rotate(' + (d.level * 3.6) + 'deg)';

    row.addEventListener('click', function () {
      ripple(row);
      selectHero(d.key);
    });
    row.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
        e.preventDefault();
        ripple(row);
        selectHero(d.key);
      }
    });
    return row;
  }

  function renderList(keys, animateIn) {
    el.list.textContent = '';
    keys.forEach(function (k) {
      const d = device(k);
      if (!d) return;
      el.list.appendChild(rowNode(d));
    });
    if (animateIn) {
      Array.prototype.forEach.call(el.list.children, function (row, i) {
        row.animate(
          [{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }],
          { duration: 320 * speed(), delay: i * 40 * speed(), easing: 'cubic-bezier(0.16, 1, 0.3, 1)', fill: 'backwards' }
        );
      });
    }
  }

  function rowKeysExcept(heroKey) {
    return DEVICES.map(function (d) { return d.key; }).filter(function (k) { return k !== heroKey; });
  }

  /* 「全部设备」展开时把主卡设备也放回列表，其余场景仍只列非主卡设备 */
  function listKeys(hk) {
    return showAll ? DEVICES.map(function (d) { return d.key; }) : rowKeysExcept(hk);
  }

  function setAllBtnOpen(on) {
    el.allBtn.setAttribute('aria-pressed', String(on));
    el.allBtn.setAttribute('aria-label', on ? '收起全部设备' : '展开全部设备');
  }

  async function toggleAll() {
    if (!DEVICES.length) { openDrawer(); return; }
    showAll = !showAll;
    setAllBtnOpen(showAll);
    await flip(function () { renderList(listKeys(heroKey), showAll); });
  }

  function snapshotList() {
    const first = new Map();
    Array.prototype.forEach.call(el.list.children, function (row) {
      first.set(row.dataset.key, row.getBoundingClientRect().top);
    });
    return first;
  }

  function reflowFrom(first) {
    if (isReduced()) return Promise.resolve();
    const sp = speed();
    const anims = [];
    Array.prototype.forEach.call(el.list.children, function (row) {
      const prev = first.get(row.dataset.key);
      if (prev == null) return;
      const dy = prev - row.getBoundingClientRect().top;
      if (Math.abs(dy) < 1) return;
      anims.push(row.animate(
        [{ transform: 'translateY(' + dy + 'px)' }, { transform: 'none' }],
        { duration: 440 * sp, easing: 'cubic-bezier(0.16, 1, 0.3, 1)' }
      ).finished.catch(function () {}));
    });
    return Promise.all(anims);
  }

  function flip(mutate) {
    if (isReduced()) { mutate(); return Promise.resolve(); }
    const first = snapshotList();
    mutate();
    return reflowFrom(first);
  }

  async function removeRow(key) {
    const row = el.list.querySelector('[data-key="' + key + '"]');
    if (!row) return;
    if (!isReduced()) {
      await row.animate(
        [{ opacity: 1, transform: 'translateY(0)' }, { opacity: 0, transform: 'translateY(6px)' }],
        { duration: 180 * speed(), easing: 'cubic-bezier(0.5, 0, 0.9, 0.4)', fill: 'forwards' }
      ).finished.catch(function () {});
    }
    row.remove();
  }

  function tweenRowLevel(key, to) {
    const row = key ? el.list.querySelector('[data-key="' + key + '"] .row-level') : null;
    if (!row) return;
    const from = parseInt(row.textContent, 10);
    if (!isFinite(from) || isReduced()) { row.textContent = to + '%'; return; }
    const t0 = performance.now();
    const total = 320 * speed();
    function tick(now) {
      const k = Math.min(1, (now - t0) / total);
      const e = 1 - Math.pow(1 - k, 3);
      row.textContent = Math.round(from + (to - from) * e) + '%';
      if (k < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  /* ---------------------------------------------------------- 杂项 UI */
  function setLive(cls, text) {
    liveCls.split(' ').forEach(function (c) { if (c) el.live.classList.remove(c); });
    liveCls = cls || '';
    liveCls.split(' ').forEach(function (c) { if (c) el.live.classList.add(c); });
    el.liveText.textContent = text;
  }

  function toast(kind, text) {
    const t = document.createElement('div');
    t.className = 'toast is-' + kind;
    t.innerHTML = '<span class="toast-dot"></span><span class="toast-text">' + text + '</span>';
    t.addEventListener('click', function () { dismissToast(t); });
    el.toasts.appendChild(t);
    while (el.toasts.children.length > 2) el.toasts.firstChild.remove();
    setTimeout(function () { dismissToast(t); }, 4200 * speed());
    return t;
  }

  function dismissToast(t) {
    if (!t || !t.parentNode) return;
    if (isReduced()) { t.remove(); return; }
    t.classList.add('is-leaving');
    setTimeout(function () { t.remove(); }, 220 * speed());
  }

  function ripple(node) {
    if (isReduced()) return;
    const panelRect = el.panel.getBoundingClientRect();
    const rect = node.getBoundingClientRect();
    const r = document.createElement('div');
    r.className = 'ripple' + (node.classList.contains('row') ? ' ripple-sm' : '');
    r.style.setProperty('--rx', (rect.left - panelRect.left + rect.width / 2) + 'px');
    r.style.setProperty('--ry', (rect.top - panelRect.top + rect.height / 2) + 'px');
    r.addEventListener('animationend', function () { r.remove(); });
    el.rippleLayer.appendChild(r);
    setTimeout(function () { if (r.parentNode) r.remove(); }, 900 * speed());
  }

  function openDrawer() {
    el.scrim.classList.add('is-shown');
    el.drawer.classList.add('is-open');
    el.drawer.setAttribute('aria-hidden', 'false');
    el.settingsBtn.setAttribute('aria-expanded', 'true');
  }
  function closeDrawer() {
    el.scrim.classList.remove('is-shown');
    el.drawer.classList.remove('is-open');
    el.drawer.setAttribute('aria-hidden', 'true');
    el.settingsBtn.setAttribute('aria-expanded', 'false');
  }

  function walk(fn) {
    // 让环境层跟随交互「让位」900ms
    fn();
    document.body.classList.add('is-attending');
    clearTimeout(walk._t);
    walk._t = setTimeout(function () { document.body.classList.remove('is-attending'); }, 900 * speed());
  }

  /* ---------------------------------------------------------- 巡演 / 强制播放
     系统开了「减少动态效果」时，浏览器会把所有动画压成静帧。
     两种解法：① 自动巡演，进页面就有东西在动；② 强制播放，用 force-motion.css
     以 !important 顶掉降级（只在本演示页生效，产品里不会这么做）。 */

  const TOUR = [
    ['calm', 2400],
    ['handoff', 1900],
    ['update', 1500],
    ['charge', 2600],
    ['low', 4400],
    ['list', 2400],
    ['drawer', 3200],
    ['notify', 2600]
  ];

  let tourOn = false;
  let tourToken = 0;

  function paintTourBtn() {
    el.tourBtn.querySelector('.rail-tour-ico').textContent = tourOn ? '❚❚' : '▶';
    el.tourBtn.querySelector('.rail-tour-txt').textContent = tourOn ? '暂停巡演' : '自动巡演';
    el.tourBtn.setAttribute('aria-pressed', tourOn ? 'true' : 'false');
  }

  async function tourLoop(tok) {
    while (true) {
      for (let i = 0; i < TOUR.length; i++) {
        if (!tourOn || tok !== tourToken) return;
        await runScenario(TOUR[i][0]);
        if (!tourOn || tok !== tourToken) return;
        await wait(TOUR[i][1]);
      }
    }
  }

  function startTour() {
    if (tourOn || isReduced()) return;
    tourOn = true;
    paintTourBtn();
    tourLoop(++tourToken);
  }

  function stopTour() {
    if (!tourOn) return;
    tourOn = false;
    tourToken++;
    paintTourBtn();
  }

  function toggleTour() {
    if (tourOn) stopTour();
    else startTour();
  }

  function syncAlert() {
    const manual = html.getAttribute('data-reduced-motion') === 'true';
    const osReduced = redMQ.matches;
    el.optForce.checked = forced();
    el.optReduced.checked = manual;

    if (manual) {
      el.motionAlert.hidden = false;
      el.motionAlertBody.innerHTML =
        '<b>已手动切到「降低动效」。</b>这里是潮息的降级形态：环境层停在静帧、' +
        '只保留 120ms 的透明度过渡——保留「变了什么」，去掉「怎么飞过来」。' +
        '<span class="motion-alert-sub">（恢复后巡演会重新开始）</span>';
      el.forceBtn.textContent = '恢复动效';
      el.forceBtn.dataset.mode = 'unreduce';
      return;
    }

    if (osReduced && forced()) {
      el.motionAlert.hidden = false;
      el.motionAlertBody.innerHTML =
        '<b>系统开启了「减少动态效果」，本页已默认强制播放。</b>否则潮息会被压成静帧，' +
        '没法评审。这是演示页的取舍，产品形态仍按系统偏好衰减。' +
        '<span class="motion-alert-sub">（想看真实降级形态，点右侧按钮或取消「强制播放」）</span>';
      el.forceBtn.textContent = '按系统降级';
      el.forceBtn.dataset.mode = 'unforce';
      return;
    }

    if (osReduced && !forced()) {
      el.motionAlert.hidden = false;
      el.motionAlertBody.innerHTML =
        '<b>系统正在「减少动态效果」。</b>潮息作为可访问性降级被压成了静帧——这是系统偏好，' +
        '不是方案本身。本页提供强制播放，仅用于评审。' +
        '<span class="motion-alert-sub">（检查 Windows：设置 → 辅助功能 → 视觉效果 → 动画效果）</span>';
      el.forceBtn.textContent = '强制播放动效';
      el.forceBtn.dataset.mode = 'force';
      return;
    }

    el.motionAlert.hidden = true;
    el.forceBtn.dataset.mode = '';
  }

  function handleForceAction() {
    const mode = el.forceBtn.dataset.mode;
    if (mode === 'unreduce') {
      html.removeAttribute('data-reduced-motion');
      el.optReduced.checked = false;
      // 回到系统默认：系统若开了减少动态效果，评审页仍恢复「默认强制播放」
      setForce(redMQ.matches);
      return;
    }
    if (mode === 'unforce') {
      html.removeAttribute('data-force-motion');
      syncAlert();
      stopTour();
      applyBase();
      return;
    }
    html.removeAttribute('data-reduced-motion');
    el.optReduced.checked = false;
    setForce(true);
  }

  function setForce(on) {
    if (on) {
      html.removeAttribute('data-reduced-motion');
      el.optReduced.checked = false;
      html.setAttribute('data-force-motion', '');
    } else {
      html.removeAttribute('data-force-motion');
    }
    syncAlert();
    if (isReduced()) stopTour();
    else {
      applyBase();
      startTour();
    }
  }

  /* ---------------------------------------------------------- 主卡选择 */
  async function selectHero(key) {
    const d = device(key);
    if (!d) return;
    const t = ++runToken;
    walk(function () {});
    await applyHero(d, { crossfade: true });
    if (t !== runToken) return;
    await flip(function () { renderList(listKeys(key), true); });
    updateSubtitle();
  }

  function updateSubtitle(extra) {
    const n = DEVICES.length;
    el.subtitle.textContent = extra || (n + ' 台设备 · 23:11 更新');
  }

  /* ---------------------------------------------------------- 基础状态 */
  function applyBase() {
    ++runToken;
    DEVICES.length = 0;
    BASE_DEVICES.forEach(function (d) { DEVICES.push(Object.assign({}, d)); });
    closeDrawer();
    el.banner.classList.remove('is-shown');
    el.banner.setAttribute('aria-hidden', 'true');
    el.toasts.textContent = '';
    el.empty.hidden = true;
    el.list.hidden = false;
    el.panel.classList.remove('is-low');
    el.heroRing.classList.remove('is-alerting');
    document.body.classList.remove('is-attending');
    setLive(BASE_LIVE.cls, BASE_LIVE.text);
    updateSubtitle();
    const hero = device(BASE_HERO);
    setRingState(stateOf(hero));
    heroContent(hero, stateOf(hero));
    shownLevel = hero.level;
    el.heroNum.textContent = String(hero.level);
    paintRing(hero.level, false);
    heroKey = BASE_HERO;
    showAll = false;
    setAllBtnOpen(false);
    renderList(rowKeysExcept(BASE_HERO));
    el.heroCard.classList.remove('is-hover-demo');
    el.refreshBtn.classList.remove('is-hover-demo');
  }

  /* ---------------------------------------------------------- 场景 */
  const SCENARIOS = [
    {
      id: 'calm', title: '常态呼吸', ms: '7.2s',
      note: '安静时只有三个东西在动：外晕、主环、在线点。相位从主环向外错开 0.35s / 0.7s，光像从心脏推出去，而不是三处各跳各的。',
      steps: [
        { l: '外晕呼吸', t: '--m-breath', ms: 7200 },
        { l: '主环呼吸', t: '--m-breath', ms: 7200 },
        { l: '环上潮痕', t: '--m-breath', ms: 7200 },
        { l: '在线点', t: '--m-breath / 2', ms: 3600 }
      ],
      tokens: ['--m-breath 7.2s', '--ease-breath', '--m-phase-card 350ms']
    },
    {
      id: 'yield', title: '静息让位', ms: '900ms',
      note: '你刚点过东西：外晕振幅降到 0.16–0.40，900ms 后回到常态。注意力在内容上时，环境主动退后一步。',
      steps: [
        { l: '外晕振幅让位', t: '--m-7', ms: 900 },
        { l: '内容保持不动', t: '—', ms: 900 }
      ],
      tokens: ['body.is-attending', '--m-7 600ms']
    },
    {
      id: 'offline', title: '离线停息', ms: '240ms',
      note: '没有信号，就不该有生命体征：呼吸层淡到 0.06 并停掉，在线点变灰、停止脉冲。离线不是「变红」，是「安静下来」。',
      steps: [
        { l: '环色转灰', t: '--m-4', ms: 240 },
        { l: '呼吸层归零', t: '--m-4', ms: 240 },
        { l: '在线点停脉冲', t: '--m-4', ms: 240 }
      ],
      tokens: ['state-offline', '--m-4 240ms']
    },
    {
      id: 'hover', title: '悬停与按下', ms: '120 / 80ms',
      note: '指针进入 120ms 到位，离开 180ms 收束，按下 80ms 压实。幅度只有 1–2px：玻璃是重的，运动必须像被手指按住，而不是弹起来。',
      steps: [
        { l: '悬停进入', t: '--m-2', ms: 120 },
        { l: '按下压实', t: '--m-1', ms: 80 },
        { l: '离开回弹', t: '--m-3', ms: 180 }
      ],
      tokens: ['--m-1 80ms', '--m-2 120ms', '--m-3 180ms', '--ease-press']
    },
    {
      id: 'update', title: '电量更新', ms: '720ms',
      note: '数字滚动、环追值、液面旋转、行内数值一起走 720ms。变化被看见，但不打断阅读——这是「日常」，不是「事件」。',
      steps: [
        { l: '环追值', t: '--m-8', ms: 720 },
        { l: '液面转向', t: '--m-8', ms: 720 },
        { l: '数字滚动', t: '--m-5', ms: 620 },
        { l: '行内数值', t: '--m-5', ms: 320 }
      ],
      tokens: ['--m-8 720ms', '--m-5 320ms', '--ease-out']
    },
    {
      id: 'charge', title: '充电接入', ms: '600ms',
      note: '充电不是把呼吸调快，而是让另一层呼吸浮现：5.4s 的电流层与 7.2s 的常态层交叉淡化 600ms，相位不会跳，同时多出一段环绕的电流弧。',
      steps: [
        { l: '双层交叉淡化', t: '--m-7', ms: 600 },
        { l: '环色转绿', t: '--m-4', ms: 240 },
        { l: '电流弧出现', t: '--m-7', ms: 600 },
        { l: '内容交接', t: '--m-5', ms: 320 }
      ],
      tokens: ['--m-breath-charge 5.4s', '--m-7 600ms', 'td-current']
    },
    {
      id: 'low', title: '低电心跳', ms: '5.4s',
      note: '低电是事件，不是永久警报：lub-dub 只跳三次，每次 1.8s；面板用预烘焙 tint 在 600ms 里慢慢泛红，同时呼吸频率降到 3.6s。心跳是「提醒你」，不是「惩罚你」。',
      steps: [
        { l: 'tint 泛红', t: '--m-7', ms: 600 },
        { l: '环色转红', t: '--m-4', ms: 240 },
        { l: '心跳 ×3', t: '--m-pulse', ms: 1800 },
        { l: '提示条', t: '--m-5', ms: 320 }
      ],
      tokens: ['--m-breath-low 3.6s', 'td-beat', '--m-7 600ms']
    },
    {
      id: 'notify', title: '提示与横幅', ms: '440ms',
      note: '横幅用 grid 行高从 0fr 到 1fr 推开列表，440ms；提示条进场 260ms、退场 180ms。两者都走「占用一点空间，再还回去」，不是瞬现瞬删。',
      steps: [
        { l: '横幅推开', t: '--m-6', ms: 440 },
        { l: '提示条进场', t: '260ms', ms: 260 },
        { l: '提示条退场', t: '--m-3', ms: 180 }
      ],
      tokens: ['--m-6 440ms', 'td-toast-in/out', '--m-3 180ms']
    },
    {
      id: 'handoff', title: '主卡交接', ms: '620ms',
      note: '换主卡不是换一张卡，是「交接」：旧内容上移淡出 160ms、新内容自下升起 320ms、环追值 720ms、数字滚动 620ms。名称、图标、传输、副标题不再瞬换。',
      steps: [
        { l: '旧内容上移', t: '160ms', ms: 160 },
        { l: '新内容升起', t: '--m-5', ms: 320 },
        { l: '数字滚动', t: '620ms', ms: 620 },
        { l: '环追值', t: '--m-8', ms: 720 }
      ],
      tokens: ['--m-5 320ms', '--m-8 720ms', '--ease-out', '--ease-in']
    },
    {
      id: 'list', title: '设备增减', ms: '440ms',
      note: '行离场 180ms 先淡出右移，再 FLIP 让其余行 440ms 归位；新行升起 320ms、按 40ms 错峰。列表高度不塌陷、不瞬跳。',
      steps: [
        { l: '行退场', t: '180ms', ms: 180 },
        { l: '其余行 FLIP', t: '--m-6', ms: 440 },
        { l: '新行升起', t: '--m-5', ms: 320 }
      ],
      tokens: ['td-row-out', '--m-6 440ms', '--m-stagger 40ms']
    },
    {
      id: 'drawer', title: '设置抽屉', ms: '440ms',
      note: '抽屉平移 320ms，遮罩淡入 240ms。遮罩不再是瞬切，背景先暗下去，抽屉才滑进来——层级被交代清楚。',
      steps: [
        { l: '遮罩淡入', t: '--m-4', ms: 240 },
        { l: '抽屉滑入', t: '--m-5', ms: 320 },
        { l: '关闭回退', t: '--m-5', ms: 320 }
      ],
      tokens: ['--m-5 320ms', '--m-4 240ms', '--ease-out']
    },
    {
      id: 'intro', title: '首次入场', ms: '600ms',
      note: '从托盘出现时，面板本身 600ms 从下浮起、轻微去模糊；内部内容按 40ms 错峰升起，总长不超过 1.15s。只播一次，会话重置不重播。',
      steps: [
        { l: '面板浮起', t: '--m-7', ms: 600 },
        { l: '标题就位', t: '40ms 延迟', ms: 320 },
        { l: '主卡就位', t: '80ms 延迟', ms: 320 },
        { l: '列表错峰', t: '120→280ms', ms: 440 }
      ],
      tokens: ['--m-7 600ms', '--m-5 320ms', '--m-stagger 40ms', 'td-panel-in']
    }
  ];

  const GROUPS = [
    { title: '环境层', ids: ['calm', 'yield', 'offline'] },
    { title: '反馈层', ids: ['hover', 'update', 'charge', 'low', 'notify'] },
    { title: '叙事层', ids: ['handoff', 'list', 'drawer', 'intro'] }
  ];

  function scenario(id) {
    for (let i = 0; i < SCENARIOS.length; i++) if (SCENARIOS[i].id === id) return SCENARIOS[i];
    return null;
  }

  function buildRail() {
    GROUPS.forEach(function (g) {
      const wrap = document.createElement('div');
      wrap.className = 'rail-group';
      const title = document.createElement('div');
      title.className = 'rail-title';
      title.textContent = g.title;
      wrap.appendChild(title);
      g.ids.forEach(function (id) {
        const sc = scenario(id);
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'rail-btn';
        b.dataset.scenario = id;
        b.innerHTML = '<span>' + sc.title + '</span><span class="rail-ms">' + sc.ms + '</span>';
        b.addEventListener('click', function () {
          stopTour();
          runScenario(id);
        });
        wrap.appendChild(b);
      });
      el.rail.appendChild(wrap);
    });
  }

  function showScenario(sc) {
    el.insTitle.textContent = sc.title;
    el.insNote.textContent = sc.note;
    el.insTokens.innerHTML = sc.tokens.map(function (t) { return '<span>' + t + '</span>'; }).join('');
    el.timeline.textContent = '';
    const max = Math.max.apply(null, sc.steps.map(function (s) { return s.ms; }));
    sc.steps.forEach(function (s) {
      const row = document.createElement('div');
      row.innerHTML =
        '<div class="tl-label"><span>' + s.l + '</span><em>' + s.t + '</em></div>' +
        '<div class="tl-track"><div class="tl-fill"></div></div>';
      el.timeline.appendChild(row);
      const fill = row.querySelector('.tl-fill');
      const pct = Math.max(4, Math.round(s.ms / max * 100));
      fill.style.width = '0%';
      fill.style.transition = 'none';
      void fill.offsetWidth;
      fill.style.transition = 'width 520ms cubic-bezier(0.16, 1, 0.3, 1)';
      fill.style.width = pct + '%';
    });
    Array.prototype.forEach.call(el.rail.querySelectorAll('.rail-btn'), function (b) {
      b.classList.toggle('is-active', b.dataset.scenario === sc.id);
    });
  }

  async function runScenario(id) {
    const sc = scenario(id);
    if (!sc) return;
    showScenario(sc);
    applyBase();
    const t = token();

    if (id === 'calm') return;

    if (id === 'yield') {
      await wait(500); if (t !== token()) return;
      document.body.classList.add('is-attending');
      await wait(2400); if (t !== token()) return;
      document.body.classList.remove('is-attending');
      return;
    }

    if (id === 'offline') {
      setLive('is-idle', '已断开');
      updateSubtitle('采集器未运行 · 数据停止更新');
      const hero = device(BASE_HERO);
      hero.offline = true;
      hero.caption = '设备离线';
      await applyHero(hero, { crossfade: true });
      if (t !== token()) return;
      Array.prototype.forEach.call(el.list.children, function (row) { row.classList.add('is-offline'); });
      hero.offline = false;
      hero.caption = '约 18 小时';
      return;
    }

    if (id === 'hover') {
      await wait(260); if (t !== token()) return;
      el.heroCard.classList.add('is-hover-demo');
      await wait(520); if (t !== token()) return;
      el.list.firstElementChild && el.list.firstElementChild.classList.add('is-hover-demo');
      await wait(620); if (t !== token()) return;
      el.refreshBtn.classList.add('is-hover-demo');
      await wait(500); if (t !== token()) return;
      el.heroCard.classList.remove('is-hover-demo');
      el.list.firstElementChild && el.list.firstElementChild.classList.remove('is-hover-demo');
      el.refreshBtn.classList.remove('is-hover-demo');
      const row = el.list.firstElementChild;
      if (row) { row.classList.add('is-press-demo'); await wait(160); row.classList.remove('is-press-demo'); }
      return;
    }

    if (id === 'update') {
      updateSubtitle('4 台设备 · 23:12 更新');
      tweenRowLevel('keychron', 88);
      tweenRowLevel('arctis', 69);
      const hero = device(BASE_HERO);
      const before = hero.level;
      hero.level = 74;
      await applyHero(hero, { crossfade: false });
      if (t !== token()) return;
      hero.level = before;
      return;
    }

    if (id === 'charge') {
      const hero = device('arctis');
      hero.charging = true;
      hero.level = 12;
      hero.caption = '充电中';
      await applyHero(hero, { crossfade: true });
      if (t !== token()) return;
      await flip(function () { renderList(rowKeysExcept('arctis')); });
      hero.charging = false;
      hero.level = 71;
      hero.caption = '约 12 小时';
      return;
    }

    if (id === 'low') {
      const hero = device('arctis');
      hero.level = 12;
      hero.caption = '约 1 小时 48 分';
      el.panel.classList.add('is-low');
      await applyHero(hero, { crossfade: true });
      if (t !== token()) return;
      el.heroRing.classList.add('is-alerting');
      setTimeout(function () { el.heroRing.classList.remove('is-alerting'); }, 3 * 1800 * speed() + 200);
      toast('low', '建议充电：Arctis Nova 7 剩余约 1 小时 48 分');
      await flip(function () { renderList(rowKeysExcept('arctis')); });
      hero.level = 71;
      hero.caption = '约 12 小时';
      return;
    }

    if (id === 'notify') {
      el.banner.classList.add('is-shown');
      el.banner.setAttribute('aria-hidden', 'false');
      updateSubtitle('采集器不可用 · 电量可能不是最新');
      const tt = toast('offline', '采集器不可用，正在重试');
      await wait(3000); if (t !== token()) return;
      el.banner.classList.remove('is-shown');
      el.banner.setAttribute('aria-hidden', 'true');
      dismissToast(tt);
      updateSubtitle();
      return;
    }

    if (id === 'handoff') {
      const hero = device('keychron');
      walk(function () {});
      await applyHero(hero, { crossfade: true });
      if (t !== token()) return;
      await flip(function () { renderList(rowKeysExcept('keychron')); });
      return;
    }

    if (id === 'list') {
      await wait(700); if (t !== token()) return;
      const first = snapshotList();
      await removeRow('keychron');
      if (t !== token()) return;
      await reflowFrom(first);
      await wait(900); if (t !== token()) return;
      const d = mkDevice({ key: 'blackshark', name: 'BlackShark V2 Pro', kind: 'headset', level: 57, transport: '2.4 GHz', left: '约 9 小时', caption: '约 9 小时' });
      DEVICES.splice(2, 0, d);
      const node = rowNode(d);
      el.list.insertBefore(node, el.list.children[2] || null);
      if (!isReduced()) {
        node.animate(
          [{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }],
          { duration: 320 * speed(), easing: 'cubic-bezier(0.16, 1, 0.3, 1)' }
        );
      }
      updateSubtitle('4 台设备 · 23:12 更新');
      return;
    }

    if (id === 'drawer') {
      await wait(420); if (t !== token()) return;
      openDrawer();
      await wait(3000); if (t !== token()) return;
      closeDrawer();
      return;
    }

    if (id === 'intro') {
      document.body.classList.remove('intro');
      void document.body.offsetWidth;
      document.body.classList.add('intro');
      setTimeout(function () { document.body.classList.remove('intro'); }, 1150 * speed() + 3300 * speed());
    }
  }

  /* ---------------------------------------------------------- 时钟 / 令牌 */
  function buildClock() {
    const axis = 16.2;
    const rows = [
      { name: '空态等待', period: 10.8, token: '--m-breath-wait' },
      { name: '呼吸', period: 7.2, token: '--m-breath' },
      { name: '充电', period: 5.4, token: '--m-breath-charge' },
      { name: '低电', period: 3.6, token: '--m-breath-low' }
    ];
    rows.forEach(function (r) {
      const row = document.createElement('div');
      row.className = 'clock-row';
      const track = document.createElement('div');
      track.className = 'clock-track';
      for (let t = 0; t <= axis + 0.01; t += r.period) {
        const tick = document.createElement('i');
        tick.className = 'clock-tick' + (t === 0 ? ' first' : '');
        tick.style.left = (t / axis * 100) + '%';
        track.appendChild(tick);
      }
      row.innerHTML = '<span class="clock-name">' + r.name + '</span>';
      row.appendChild(track);
      row.insertAdjacentHTML('beforeend', '<span class="clock-val">' + r.period.toFixed(1) + 's ×' + (r.period === 10.8 ? '6' : r.period === 7.2 ? '4' : r.period === 5.4 ? '3' : '2') + '</span>');
      el.clockGrid.appendChild(row);
    });
    const axisEl = document.createElement('div');
    axisEl.className = 'clock-axis';
    axisEl.innerHTML = '<span>秒</span><div class="axis-ticks"><i style="left:0">0</i><i style="left:33.3333%">5.4</i><i style="left:66.6667%">10.8</i><i style="left:100%">16.2</i></div><span></span>';
    el.clockGrid.appendChild(axisEl);
  }

  const DURATIONS = [
    { t: '--m-0', use: '焦点环', ms: 0 },
    { t: '--m-1', use: '按下', ms: 80 },
    { t: '--m-2', use: '悬停进入', ms: 120 },
    { t: '--m-3', use: '悬停离开 / 回弹', ms: 180 },
    { t: '--m-4', use: '状态色 / 标签', ms: 240 },
    { t: '--m-5', use: '内容承接', ms: 320 },
    { t: '--m-6', use: '抽屉 / 横幅 / 重排', ms: 440 },
    { t: '--m-7', use: '入场 / 交接', ms: 600 },
    { t: '--m-8', use: '环追值 / 收束', ms: 720 }
  ];

  const EASINGS = [
    { t: '--ease-out', use: '到达', curve: '.16,1,.3,1' },
    { t: '--ease-in', use: '离场', curve: '.5,0,.9,.4' },
    { t: '--ease-in-out', use: '空间位移', curve: '.65,0,.35,1' },
    { t: '--ease-breath', use: '呼吸', curve: '.45,0,.55,1' },
    { t: '--ease-press', use: '按下', curve: '.3,0,.3,1' }
  ];

  function curveSVG(curve, size) {
    const c = curve.split(',').map(Number);
    const s = size || 30;
    const p = s - 3;
    const x1 = 1.5 + c[0] * p, y1 = 1.5 + (1 - c[1]) * p;
    const x2 = 1.5 + c[2] * p, y2 = 1.5 + (1 - c[3]) * p;
    return '<svg class="ease-curve" viewBox="0 0 ' + s + ' ' + s + '" aria-hidden="true">' +
      '<path d="M1.5 ' + (1.5 + p) + ' C ' + x1 + ' ' + y1 + ', ' + x2 + ' ' + y2 + ', ' + (1.5 + p) + ' 1.5"/>' +
      '<circle cx="1.5" cy="' + (1.5 + p) + '" r="1.6"/></svg>';
  }

  function buildTokens() {
    DURATIONS.forEach(function (d) {
      const row = document.createElement('div');
      row.className = 'token-row';
      row.innerHTML =
        '<span class="token-name">' + d.t + '</span>' +
        '<span class="token-use">' + d.use + '</span>' +
        '<span class="demo-track"><i class="demo-dot"></i></span>' +
        '<span class="token-val">' + d.ms + 'ms</span>' +
        '<button type="button" class="token-play" aria-label="播放 ' + d.t + '"></button>';
      el.durTable.appendChild(row);
      row.querySelector('.token-play').addEventListener('click', function () {
        demoDot(row, Math.max(d.ms, 120), 'cubic-bezier(0.16, 1, 0.3, 1)');
      });
    });

    EASINGS.forEach(function (e) {
      const row = document.createElement('div');
      row.className = 'token-row is-easing';
      row.innerHTML =
        '<span class="ease-cell">' + curveSVG(e.curve) + '</span>' +
        '<span class="token-name">' + e.t + '</span>' +
        '<span class="token-use">' + e.use + '</span>' +
        '<span class="demo-track"><i class="demo-dot"></i></span>' +
        '<button type="button" class="token-play" aria-label="播放 ' + e.t + '"></button>';
      el.easeTable.appendChild(row);
      row.querySelector('.token-play').addEventListener('click', function () {
        demoDot(row, 900, 'cubic-bezier(' + e.curve + ')');
      });
    });
  }

  function demoDot(row, ms, easing) {
    const track = row.querySelector('.demo-track');
    const dot = track.querySelector('.demo-dot');
    const dist = track.clientWidth - 8;
    if (isReduced()) return;
    dot.getAnimations().forEach(function (a) { a.cancel(); });
    dot.animate([{ transform: 'translateX(0)' }, { transform: 'translateX(' + dist + 'px)' }], {
      duration: ms * speed(), easing: easing, fill: 'both'
    });
  }

  /* ---------------------------------------------------------- 指针柔光 */
  (function specular() {
    if (isReduced()) return;
    function move(e) {
      const r = el.stage.getBoundingClientRect();
      const x = e.clientX - r.left;
      const y = e.clientY - r.top;
      el.specular.style.transform = 'translate3d(' + x + 'px,' + y + 'px,0)';
    }
    el.stage.addEventListener('pointerenter', function (e) {
      el.glowLayer.classList.add('is-tracking');
      move(e);
    });
    el.stage.addEventListener('pointermove', move);
    el.stage.addEventListener('pointerleave', function () {
      el.glowLayer.classList.remove('is-tracking');
      el.specular.style.transform = 'translate3d(-1000px,-1000px,0)';
    });
  })();

  /* ---------------------------------------------------------- 控件 */
  function bindChrome() {
    el.refreshBtn.addEventListener('click', function () {
      if (isReduced()) return;
      const svg = el.refreshBtn.querySelector('svg');
      svg.getAnimations().forEach(function (a) { a.cancel(); });
      svg.animate([{ transform: 'rotate(0deg)' }, { transform: 'rotate(360deg)' }], {
        duration: 600 * speed(), easing: 'cubic-bezier(0.16, 1, 0.3, 1)'
      });
    });

    el.pinBtn.addEventListener('click', function () {
      const on = el.pinBtn.getAttribute('aria-pressed') !== 'true';
      el.pinBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
      el.pinBtn.classList.toggle('is-on', on);
    });

    el.settingsBtn.addEventListener('click', openDrawer);
    el.drawerClose.addEventListener('click', closeDrawer);
    el.scrim.addEventListener('click', closeDrawer);
    el.allBtn.addEventListener('click', function () { ripple(el.allBtn); toggleAll(); });

    el.panel.addEventListener('pointerdown', function () { walk(function () {}); }, true);

    el.optReduced.addEventListener('change', function () {
      if (el.optReduced.checked) {
        // 手动降级优先于「强制播放」：同时撤掉 force 覆盖，否则 !important 会把降级顶回去
        html.removeAttribute('data-force-motion');
        html.setAttribute('data-reduced-motion', 'true');
        stopTour();
      } else {
        html.removeAttribute('data-reduced-motion');
      }
      syncAlert();
      if (!isReduced() && !tourOn) {
        applyBase();
        startTour();
      }
    });
    el.optForce.addEventListener('change', function () { setForce(el.optForce.checked); });
    el.forceBtn.addEventListener('click', handleForceAction);
    el.tourBtn.addEventListener('click', toggleTour);
    if (redMQ.addEventListener) {
      redMQ.addEventListener('change', function () {
        syncAlert();
        if (isReduced()) stopTour();
      });
    }
    el.optSlow.addEventListener('change', function () {
      html.style.setProperty('--td-speed', el.optSlow.checked ? '4' : '1');
    });
    el.optPause.addEventListener('change', function () {
      document.getAnimations().forEach(function (a) {
        if (el.optPause.checked) a.pause(); else a.play();
      });
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') closeDrawer();
    });
  }

  /* ---------------------------------------------------------- 启动 */
  function boot() {
    buildRail();
    buildClock();
    buildTokens();
    bindChrome();
    // 系统开着「减少动态效果」时默认强制播放：这是评审页，整页静止等于看不到方案。
    // 用户可以点提示条上的「按系统降级」或取消「强制播放」回到真实降级形态。
    if (redMQ.matches && !forced()) html.setAttribute('data-force-motion', '');
    syncAlert();
    applyBase();
    showScenario(scenario('calm'));
    el.rail.querySelector('[data-scenario="calm"]').classList.add('is-active');
    document.body.classList.add('intro');
    setTimeout(function () { document.body.classList.remove('intro'); }, 1150 * speed());
    // 进页面就开始巡演：先让眼睛看到「时间」，再让人去读规格。
    if (!isReduced()) {
      startTour();
    } else {
      paintTourBtn();
    }
  }

  boot();
})();
