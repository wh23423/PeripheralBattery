/* ============================================================
   bridge.js — 前端传输层（无框架、无构建器）
   职责：
   · Tauri：window.__TAURI__（withGlobalTauri=true）存在时 invoke / listen
   · 浏览器：?demo 静态演示、?mock 确定性事件流；window.__pbMock 供 Playwright 驱动
   事件名/命令名严格按 protocol.md §4。
   ============================================================ */
(function () {
  'use strict';

  var params = new URLSearchParams(location.search);
  var tauri = window.__TAURI__ || null;
  /* withGlobalTauri=true 时 __TAURI__ 一定存在；再用 internals / tauri: 协议兜底，
     避免 Tauri 里因注入顺序问题误判成浏览器 demo 模式而渲染假数据。 */
  var tauriProto = location.protocol === 'tauri:' ||
    location.hostname === 'tauri.localhost' ||
    location.hostname === 'ipc.localhost';
  var hasTauri = !!tauri || !!window.__TAURI_INTERNALS__ || tauriProto;
  var effects = params.get('effects') || '';
  var forceMock = params.has('mock');
  var forceDemo = params.has('demo');
  var freeze = params.has('freeze') || params.get('mock') === 'freeze';

  var root = document.documentElement;
  if (!root.dataset.windows11) root.dataset.windows11 = 'true';
  if (effects) root.dataset.effects = effects;
  root.dataset.tauri = hasTauri ? 'true' : 'false';

  /* ---------------------------------------------------------- 事件总线 */
  var handlers = new Map();

  function on(name, fn) {
    var set = handlers.get(name);
    if (!set) { set = new Set(); handlers.set(name, set); }
    set.add(fn);
    return function off() { set.delete(fn); };
  }

  function emit(name, payload) {
    var set = handlers.get(name);
    if (!set) return;
    set.forEach(function (fn) {
      try { fn(payload); }
      catch (err) { debug.errors.push({ where: 'handler:' + name, message: String(err && err.message || err) }); }
    });
  }

  var debug = {
    mode: hasTauri ? 'tauri' : 'browser',
    scenario: '',
    calls: [],
    errors: [],
    lastTray: null,
    pinned: false,
    collectorState: null,
    events: [],
    params: {
      mock: forceMock, demo: forceDemo, freeze: freeze,
      effects: effects, hide: params.has('hide')
    }
  };

  /* ---------------------------------------------------------- Tauri 原生 */
  var invokeFn = tauri ? ((tauri.core && tauri.core.invoke) || tauri.invoke || null) : null;
  var listenFn = tauri ? ((tauri.event && tauri.event.listen) || tauri.listen || null) : null;
  var unlisteners = [];

  function rememberCall(cmd, args, ok, error) {
    debug.calls.push({ cmd: cmd, args: args || null, ok: ok, error: error || null, at: Date.now() });
    if (debug.calls.length > 200) debug.calls.shift();
  }

  async function invoke(cmd, args) {
    var a = args || {};
    if (hasTauri && invokeFn) {
      try {
        var result = await (args ? invokeFn(cmd, a) : invokeFn(cmd));
        rememberCall(cmd, a, true, null);
        return result;
      } catch (err) {
        rememberCall(cmd, a, false, String((err && err.message) || err));
        return null;
      }
    }
    if (hasTauri && !invokeFn) {
      rememberCall(cmd, a, false, 'window.__TAURI__ 不可用');
      return Promise.resolve(null);
    }
    return simulateInvoke(cmd, a);
  }

  /* 浏览器下把 invoke 变成可观测的模拟：测试断言断言用，不抛错 */
  function simulateInvoke(cmd, args) {
    rememberCall(cmd, args, true, null);
    switch (cmd) {
      case 'ui_ready':
        return Promise.resolve({ show: true });
      case 'collector_send': {
        var line = typeof args.line === 'string' ? args.line : '';
        var cmdObj = null;
        try { cmdObj = JSON.parse(line); } catch (e) { cmdObj = null; }
        if (cmdObj && cmdObj.t === 'refresh' && scenario === 'mock') {
          mockSeq += 1;
          emitCollector({ t: 'tick', session: mockSession, seq: mockSeq, devices: mockSnapshot() });
        }
        return Promise.resolve(true);
      }
      case 'collector_restart':
        if (scenario === 'mock') scheduleRestart();
        return Promise.resolve(true);
      case 'set_pinned':
        debug.pinned = !!args.pinned;
        setTimeout(function () { emit('app-state', appStatePayload()); }, 0);
        return Promise.resolve(true);
      case 'set_tray_state':
        debug.lastTray = { level: args.level == null ? null : args.level, charging: !!args.charging, online: !!args.online, kind: args.kind || '' };
        return Promise.resolve();
      case 'window_action':
        if (args.action === 'hide') setTimeout(function () { emit('window-visibility', { hidden: true }); }, 0);
        return Promise.resolve();
      case 'collector_status':
        return Promise.resolve(debug.collectorState || { state: scenario === 'mock' ? 'ready' : 'starting' });
      default:
        return Promise.resolve(null);
    }
  }

  function appStatePayload() {
    return {
      pinned: !!debug.pinned,
      platform: { windows11: root.dataset.windows11 !== 'false', build: 22631 },
      build: debug.build || 'mock',
      version: '0.1.0'
    };
  }

  /* ---------------------------------------------------------- 确定性数据 */
  function dev(d) {
    return {
      key: d.key,
      name: d.name,
      level: d.level == null ? null : d.level,
      approx: d.approx || '',
      left_s: d.left_s == null ? null : d.left_s,
      charging: !!d.charging,
      online: d.online !== false,
      kind: d.kind || 'bluetooth',
      transport: d.transport || 'unknown',
      via: d.via || '',
      updated_unix: d.updated_unix || 1760000000
    };
  }

  var DEMO_DEVICES = [
    dev({ key: 'headset:arctis-nova-7', name: 'Arctis Nova 7', kind: 'headset', transport: '2.4g', level: 12, left_s: 6480 }),
    dev({ key: 'gamepad:xbox-elite-2', name: 'Xbox Elite 2', kind: 'gamepad', transport: 'bluetooth', level: 45, charging: true }),
    dev({ key: 'mouse:viper-v3-pro', name: 'Viper V3 Pro', kind: 'mouse', transport: '2.4g', level: 68, left_s: 64800 }),
    dev({ key: 'keyboard:keychron-q1-pro', name: 'Keychron Q1 Pro', kind: 'keyboard', transport: 'bluetooth', level: 92, left_s: 259200 }),
    dev({ key: 'gamepad:8bitdo-ultimate', name: '8BitDo Ultimate', kind: 'gamepad', transport: 'bluetooth', level: null, left_s: null })
  ];

  var MOCK_DEVICES = [
    dev({ key: 'logi:046d:c547:unit-12', name: 'Arctis Nova 7', kind: 'headset', transport: '2.4g', level: 12, left_s: 6480 }),
    dev({ key: 'xinput:045e:0b00:unit-1', name: 'Xbox Elite 2', kind: 'gamepad', transport: 'bluetooth', level: 45, charging: true }),
    dev({ key: 'razer:1532:0099:unit-3', name: 'Viper V3 Pro', kind: 'mouse', transport: '2.4g', level: 68, left_s: 64800 }),
    dev({ key: 'logi:046d:c092:unit-77', name: 'Logi M185', kind: 'mouse', transport: 'unknown', level: null, approx: '', left_s: null }),
    dev({ key: 'steelseries:1038:1720:unit-5', name: 'Arctis 7P', kind: 'headset', transport: 'usb', level: 55, approx: '约 55%（中）', left_s: null })
  ];

  var scenario = '';            /* 'demo' | 'mock' | ''（Tauri 等真实事件） */
  var mockSession = 'mock-session-1';
  var mockSeq = 0;
  var mockDevices = new Map();
  var demoTimerIds = [];

  function resetMockDevices(list) {
    mockDevices = new Map();
    (list || []).forEach(function (d) { mockDevices.set(d.key, dev(d)); });
  }

  function mockSnapshot() {
    return Array.from(mockDevices.values()).map(function (d) { return dev(d); });
  }

  function emitCollector(evt) {
    debug.events.push({ t: evt.t, seq: evt.seq, session: evt.session });
    if (debug.events.length > 400) debug.events.shift();
    emit('collector-message', JSON.stringify(evt));
  }

  function nextSeq() { mockSeq += 1; return mockSeq; }

  /* mock 自动剧本用；freeze 时完全不排期，测试手动驱动 */
  function schedule(fn, ms) {
    if (freeze) return;
    var id = window.setTimeout(function () {
      demoTimerIds = demoTimerIds.filter(function (x) { return x !== id; });
      if (scenario === 'mock') fn();
    }, ms);
    demoTimerIds.push(id);
  }

  function startMockTimeline() {
    schedule(function () {
      var d = mockDevices.get('razer:1532:0099:unit-3');
      if (!d) return;
      d.level = 70;
      emitCollector({ t: 'device', session: mockSession, seq: nextSeq(), device: dev(d) });
    }, 140);

    schedule(function () {
      emitCollector({
        t: 'alert', session: mockSession, seq: nextSeq(), kind: 'low',
        key: 'logi:046d:c547:unit-12', level: 12, text: 'Arctis Nova 7 电量低，建议充电'
      });
    }, 380);

    schedule(function () {
      var d = mockDevices.get('logi:046d:c547:unit-12');
      if (!d) return;
      d.level = 11;
      emitCollector({ t: 'device', session: mockSession, seq: nextSeq(), device: dev(d) });
    }, 720);

    /* replaces：key 迁移（UI 必须 morph，不 delete+insert） */
    schedule(function () {
      var oldKey = 'logi:046d:c092:unit-77';
      var d = mockDevices.get(oldKey);
      if (!d) return;
      mockDevices.delete(oldKey);
      var nd = dev({
        key: 'logi:046d:c092:unit-99', name: 'Logi M185（已识别）', kind: 'mouse',
        transport: '2.4g', level: 64, left_s: 36000
      });
      mockDevices.set(nd.key, nd);
      emitCollector({ t: 'device', session: mockSession, seq: nextSeq(), device: nd, replaces: oldKey });
    }, 1100);

    /* gone：由全局 tick 依据 last_seen 发出 */
    schedule(function () {
      var key = 'steelseries:1038:1720:unit-5';
      if (!mockDevices.has(key)) return;
      mockDevices.delete(key);
      emitCollector({ t: 'gone', session: mockSession, seq: nextSeq(), key: key });
    }, 1600);

    /* 重新出现 */
    schedule(function () {
      var d = dev({ key: 'steelseries:1038:1720:unit-5', name: 'Arctis 7P', kind: 'headset', transport: 'usb', level: 54, approx: '约 54%（中）' });
      mockDevices.set(d.key, d);
      emitCollector({ t: 'device', session: mockSession, seq: nextSeq(), device: d });
    }, 2300);
  }

  function scheduleRestart() {
    window.setTimeout(function () {
      debug.collectorState = { state: 'starting' };
      emit('collector-state', debug.collectorState);
      window.setTimeout(function () {
        mockSession = 'mock-session-' + (Date.now() % 100000);
        mockSeq = 0;
        resetMockDevices(MOCK_DEVICES);
        debug.collectorState = { state: 'ready', pid: 4242, protocol: 1, session: mockSession };
        emit('collector-state', debug.collectorState);
        emitCollector({
          t: 'hello', protocol: 1, session: mockSession, build: 'mock', capabilities: ['approx', 'left_s', 'via', 'transport', 'replaces'],
          devices: mockSnapshot(),
          settings: { poll: { mouse: 5, keyboard: 15, headset: 15, gamepad: 10, steam: 30 }, providers: ['logitech', 'razer', 'keychron'] }
        });
      }, 120);
    }, 60);
  }

  /* ---------------------------------------------------------- 浏览器剧本 */
  function startScenario() {
    if (scenario === 'demo') {
      emitCollector({
        t: 'hello', protocol: 1, session: 'demo-session-1', build: 'demo', capabilities: ['approx', 'left_s', 'via', 'transport', 'replaces'],
        devices: DEMO_DEVICES.map(function (d) { return dev(d); }),
        settings: { poll: { mouse: 5, keyboard: 15, headset: 15, gamepad: 10, steam: 30 }, providers: ['logitech', 'razer'] }
      });
      return;
    }
    if (scenario === 'mock') {
      /* ?mock&empty：hello 正常但 0 台设备，用于测「首次数据到达前不要下发 offline」 */
      resetMockDevices(params.has('empty') ? [] : MOCK_DEVICES);
      debug.collectorState = { state: 'ready', pid: 4242, protocol: 1, session: mockSession };
      emit('collector-state', debug.collectorState);
      emitCollector({
        t: 'hello', protocol: 1, session: mockSession, build: 'mock', capabilities: ['approx', 'left_s', 'via', 'transport', 'replaces'],
        devices: mockSnapshot(),
        settings: { poll: { mouse: 5, keyboard: 15, headset: 15, gamepad: 10, steam: 30 }, providers: ['logitech', 'razer', 'keychron'] }
      });
      if (!params.has('empty')) startMockTimeline();
    }
  }

  /* ---------------------------------------------------------- 启动 */
  async function start() {
    debug.scenario = scenario;
    if (!hasTauri) document.body.classList.add('demo');

    if (hasTauri && listenFn) {
      var names = ['collector-message', 'collector-state', 'app-state', 'tray-action', 'window-visibility'];
      for (var i = 0; i < names.length; i++) {
        (function (name) {
          try {
            var p = listenFn(name, function (evt) { emit(name, evt && evt.payload !== undefined ? evt.payload : evt); });
            if (p && typeof p.then === 'function') p.then(function (un) { if (typeof un === 'function') unlisteners.push(un); }, function () {});
          } catch (err) { debug.errors.push({ where: 'listen:' + name, message: String(err && err.message || err) }); }
        })(names[i]);
      }
      /* 先拿一次 sidecar 状态，再让 app 渲染首帧 */
      var status = await invoke('collector_status');
      if (status) emit('collector-state', status);
    }

    if (scenario === 'demo' || scenario === 'mock') startScenario();

    /* 浏览器预览：补一次 app-state，让设置面板有版本/平台信息 */
    if (!hasTauri) {
      debug.build = scenario || 'demo';
      setTimeout(function () { emit('app-state', appStatePayload()); }, 0);
    }

    /* ?hide：模拟 Rust 的 window-visibility {hidden:true}（截图/测试用） */
    if (params.has('hide')) {
      window.setTimeout(function () { emit('window-visibility', { hidden: true }); }, 420);
    }
  }

  /* 首帧完成后调用：Rust 此时才显示窗口（protocol §4.2） */
  async function ready() {
    if (hasTauri) await invoke('ui_ready');
  }

  /* ---------------------------------------------------------- 测试控制面 */
  function mockFill(evt) {
    var ev = Object.assign({}, evt);
    if (ev.t === 'hello') {
      if (!ev.session) ev.session = mockSession;
      if (ev.protocol == null) ev.protocol = 1;
      if (!ev.devices) ev.devices = mockSnapshot();
      if (ev.seq == null) ev.seq = 0;
    } else {
      if (!ev.session) ev.session = mockSession;
      if (ev.seq == null) ev.seq = nextSeq();
    }
    return ev;
  }

  function mockDevice(device, replaces) {
    var d = dev(device);
    if (replaces) mockDevices.delete(replaces);
    mockDevices.set(d.key, d);
    var evt = mockFill({ t: 'device', device: d, replaces: replaces });
    emitCollector(evt);
    return evt;
  }

  function mockSetLevel(key, level) {
    var d = mockDevices.get(key);
    if (!d) return null;
    d.level = level;
    return mockDevice(d);
  }

  function makeMockApi() {
    return {
      emit: function (evt) { emitCollector(mockFill(evt)); },
      hello: function (opts) {
        opts = opts || {};
        if (opts.session) mockSession = opts.session;
        mockSeq = 0;
        var devices = opts.devices ? opts.devices.map(dev) : mockSnapshot();
        resetMockDevices(devices);
        debug.collectorState = { state: 'ready', pid: 4242, protocol: opts.protocol == null ? 1 : opts.protocol, session: mockSession };
        emit('collector-state', debug.collectorState);
        emitCollector({ t: 'hello', protocol: opts.protocol == null ? 1 : opts.protocol, session: mockSession, build: opts.build || 'mock', capabilities: ['approx', 'left_s', 'via', 'transport', 'replaces'], devices: devices, settings: opts.settings || {} });
      },
      device: function (device, replaces) { return mockDevice(device, replaces); },
      gone: function (key) {
        mockDevices.delete(key);
        var evt = mockFill({ t: 'gone', key: key });
        emitCollector(evt);
        return evt;
      },
      tick: function (devices) {
        var list = devices ? devices.map(dev) : mockSnapshot();
        resetMockDevices(list);
        var evt = mockFill({ t: 'tick', devices: list });
        emitCollector(evt);
        return evt;
      },
      alert: function (kind, key, level, text) {
        var evt = mockFill({ t: 'alert', kind: kind || 'low', key: key || '', level: level == null ? null : level, text: text || '' });
        emitCollector(evt);
        return evt;
      },
      stat: function (payload) {
        var evt = mockFill(Object.assign({ t: 'stat' }, payload || {}));
        emitCollector(evt);
        return evt;
      },
      visibility: function (hidden) { emit('window-visibility', { hidden: !!hidden }); },
      collectorState: function (state) {
        debug.collectorState = state || null;
        emit('collector-state', state);
      },
      appState: function (state) { emit('app-state', state); },
      trayAction: function (action) { emit('tray-action', { action: action }); },
      setLevel: function (key, level) { return mockSetLevel(key, level); },
      stressUpdates: function (key, n) {
        var d = mockDevices.get(key);
        if (!d) return 0;
        for (var i = 0; i < n; i++) {
          d.level = 30 + ((i * 7) % 60);
          mockSeq += 1;
          emitCollector({ t: 'device', session: mockSession, seq: mockSeq, device: dev(d) });
        }
        return n;
      },
      allOffline: function () {
        var list = mockSnapshot().map(function (d) { d.online = false; return d; });
        return this.tick(list);
      },
      sessionReset: function (session) { this.hello({ session: session || ('mock-session-' + Date.now()) }); },
      devices: function () { return mockSnapshot(); },
      setPinnedLocal: function (pinned) { debug.pinned = !!pinned; emit('app-state', appStatePayload()); },
      cancelTimers: function () {
        demoTimerIds.forEach(function (id) { clearTimeout(id); });
        demoTimerIds = [];
      }
    };
  }

  /* ---------------------------------------------------------- 对外 API */
  var bridge = {
    hasTauri: hasTauri,
    scenario: scenario,
    effects: effects,
    debug: debug,
    on: on,
    emit: emit,
    invoke: invoke,
    start: start,
    ready: ready,
    call: function (cmd, args) { return invoke(cmd, args); }
  };

  if (!hasTauri) {
    bridge.scenario = forceMock ? 'mock' : 'demo';
    scenario = bridge.scenario;
  } else if (forceMock || forceDemo) {
    bridge.scenario = forceMock ? 'mock' : 'demo';
    scenario = bridge.scenario;
  } else {
    bridge.scenario = '';
    scenario = '';
  }

  window.PB = window.PB || {};
  window.PB.bridge = bridge;
  window.__pbBridge = debug;

  /* 测试/调试控制面：浏览器始终可用；Tauri + ?mock 也可用 */
  window.__pbMock = makeMockApi();
})();
