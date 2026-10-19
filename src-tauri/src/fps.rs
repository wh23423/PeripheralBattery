//! FPS 测量钩子（仅测试用，生产无影响）。
//!
//! 环境变量 `PB_FPS_PROBE=<ms>`：面板显示后 500ms 注入一段 JS，用 rAF 采样 `<ms>` 毫秒的
//! 帧间隔，收集 rows 数 / panel rect / dpr / inner size / backdropFilter，最后调用
//! `window.__TAURI__.core.invoke('report_fps', {data: JSON.stringify(result)})`，
//! 由 Rust 命令写 `FPS_PROBE {json}` 到 app.log。release 构建同样生效。

use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use tauri::{AppHandle, Manager};

static ARMED: AtomicBool = AtomicBool::new(false);

const PROBE_JS: &str = r#"
(() => {
  const duration = __DURATION__;
  const samples = [];
  let last = 0;
  let start = 0;
  function report() {
    const sorted = samples.slice().sort((a, b) => a - b);
    const sum = sorted.reduce((a, b) => a + b, 0);
    const pct = (p) => sorted.length ? sorted[Math.min(sorted.length - 1, Math.max(0, Math.floor(sorted.length * p)))] : null;
    const panel = document.getElementById('panel') || document.querySelector('.panel');
    const rect = panel ? panel.getBoundingClientRect() : null;
    const cs = panel ? getComputedStyle(panel) : null;
    const result = {
      duration_ms: duration,
      frames: sorted.length,
      fps: sorted.length ? Number((1000 / (sum / sorted.length)).toFixed(2)) : null,
      avg_ms: sorted.length ? Number((sum / sorted.length).toFixed(2)) : null,
      p50_ms: pct(0.5),
      p95_ms: pct(0.95),
      p99_ms: pct(0.99),
      max_ms: sorted.length ? sorted[sorted.length - 1] : null,
      rows: document.querySelectorAll('#list .row').length,
      panel_rect: rect ? { x: rect.x, y: rect.y, w: rect.width, h: rect.height } : null,
      dpr: window.devicePixelRatio,
      inner: { w: window.innerWidth, h: window.innerHeight },
      backdrop_filter: cs ? cs.backdropFilter : null,
      hidden: document.hidden,
      visibility: document.visibilityState,
      tauri: !!window.__TAURI__,
      ts: Date.now()
    };
    try {
      window.__TAURI__.core.invoke('report_fps', { data: JSON.stringify(result) });
    } catch (e) {
      try { console.error('FPS_PROBE invoke failed', e); } catch (_) {}
    }
  }
  function tick(ts) {
    if (!start) { start = ts; last = ts; }
    else { samples.push(ts - last); last = ts; }
    if (ts - start < duration) requestAnimationFrame(tick);
    else report();
  }
  requestAnimationFrame(tick);
})();
"#;

/// 在 show_panel 之后调用：若设置了 PB_FPS_PROBE 且尚未采样，则 500ms 后注入探针。
pub fn arm_after_show(app: &AppHandle) {
    let Some(duration_ms) = crate::state::env_ms("PB_FPS_PROBE") else {
        return;
    };
    if ARMED.swap(true, Ordering::SeqCst) {
        return;
    }
    crate::log_info!("PB_FPS_PROBE={duration_ms}ms armed (inject 500ms after first show)");
    let handle = app.clone();
    let _ = std::thread::Builder::new()
        .name("fps-probe".into())
        .spawn(move || {
            std::thread::sleep(Duration::from_millis(500));
            let h = handle.clone();
            let _ = handle.run_on_main_thread(move || {
                let Some(win) = h.get_webview_window("main") else {
                    crate::log_warn!("FPS_PROBE: main window 不存在");
                    return;
                };
                if !h.state::<crate::state::AppState>().window_visible() {
                    crate::log_warn!("FPS_PROBE: 面板不可见，跳过注入");
                    return;
                }
                let js = PROBE_JS.replace("__DURATION__", &duration_ms.to_string());
                match win.eval(&js) {
                    Ok(()) => crate::log_info!(
                        "FPS_PROBE injected duration={duration_ms}ms (结果将以 FPS_PROBE {{json}} 写入本日志)"
                    ),
                    Err(e) => crate::log_warn!("FPS_PROBE eval 失败: {e}"),
                }
            });
        });
}
