//! 面板显示/隐藏：隐藏四步（IsVisible → 事件 → window.hide）、托盘附近定位、
//! 失焦 270ms 抑制 + GetForegroundWindow 校验、DPI 变化重设尺寸（协议 §4.3 / BUILD_PLAN §3.1）。

use std::time::Duration;

use serde_json::json;
use tauri::{
    AppHandle, Emitter, LogicalSize, Manager, PhysicalPosition, WebviewWindow, WindowEvent,
};

use crate::platform;
use crate::state::AppState;

fn log_window_metrics(win: &WebviewWindow, phase: &str) {
    let scale = win.scale_factor().unwrap_or(1.0);
    match (win.outer_position(), win.outer_size()) {
        (Ok(pos), Ok(size)) => crate::log_info!(
            "window {phase} origin=({},{}) size={}x{} scale={scale:.3} dip=({:.1}x{:.1})",
            pos.x,
            pos.y,
            size.width,
            size.height,
            size.width as f64 / scale,
            size.height as f64 / scale
        ),
        _ => crate::log_warn!("window {phase} metrics unavailable"),
    }
}

/// 平台窗口装饰：Win11 DWM 圆角；Win10 关掉 acrylic（回退不透明，前端按 windows11=false 走实底）。
pub fn apply_platform(app: &AppHandle, windows11: bool, windows_build: u32) {
    let Some(win) = app.get_webview_window("main") else {
        crate::log_error!("apply_platform: main window 不存在");
        return;
    };
    // 缓存 DPI scale：侧车线程渲染托盘图标时只读缓存，不跨线程查窗口。
    let scale = win.scale_factor().unwrap_or(1.0);
    app.state::<AppState>().set_scale_factor(scale);
    crate::log_info!("scale factor cached = {scale:.3}");
    if windows11 {
        match platform::window_hwnd(&win) {
            Some(hwnd) => {
                let hr = platform::set_dwm_round_corner(platform::raw_hwnd(hwnd));
                crate::log_info!(
                    "DWM round corner build={windows_build} hwnd=0x{hwnd:x} hresult=0x{:08x}",
                    hr as u32
                );
            }
            None => crate::log_warn!("DWM round corner: 无法获取 HWND"),
        }
    } else {
        match win.set_effects::<Option<tauri::utils::config::WindowEffectsConfig>>(None) {
            Ok(()) => crate::log_info!(
                "win10 fallback build={windows_build}: acrylic disabled, frontend uses opaque background"
            ),
            Err(e) => crate::log_warn!("win10 fallback: 关闭 acrylic 失败: {e}"),
        }
    }
    log_window_metrics(&win, "created");
}

/// 优先用托盘图标矩形定位；`tray.rect()` 不可用时退回光标附近。
fn query_tray_rect(app: &AppHandle, scale: f64) -> Option<(i32, i32, i32, i32)> {
    let state = app.state::<AppState>();
    // 克隆后立即释放 tray 锁：tray.rect() 不能在持锁时调用（避免跨线程互相等待）。
    let tray = state.tray_clone()?;
    match tray.rect() {
        Ok(Some(rect)) => {
            let pos = rect.position.to_physical::<i32>(scale);
            let size = rect.size.to_physical::<u32>(scale);
            if size.width == 0 || size.height == 0 {
                crate::log_debug!("tray.rect() 尺寸为 0，视为不可用 → 光标兜底");
                return None;
            }
            crate::log_debug!(
                "tray.rect() = ({},{} {}x{})",
                pos.x,
                pos.y,
                size.width,
                size.height
            );
            Some((pos.x, pos.y, size.width as i32, size.height as i32))
        }
        Ok(None) => {
            crate::log_debug!("tray.rect() 不可用 → 光标兜底");
            None
        }
        Err(e) => {
            crate::log_debug!("tray.rect() 失败({e}) → 光标兜底");
            None
        }
    }
}

/// 位置：优先托盘矩形（点击事件记录 / tray.rect()），其次光标附近；夹在所在显示器工作区内。
/// 尺寸一律按固定逻辑尺寸 780×440 换算物理像素：窗口最小化时 `outer_size()` 会返回
/// 199×34 之类的垃圾值，不能用于定位（audit P1-1）。
fn position_panel(app: &AppHandle, win: &WebviewWindow) {
    let state = app.state::<AppState>();
    let scale = win.scale_factor().unwrap_or(1.0);
    let size: tauri::PhysicalSize<u32> = LogicalSize::new(780.0, 440.0).to_physical(scale);
    let (anchor_src, (ax, ay, aw, ah)) = match state.tray_rect() {
        Some(rect) => ("tray-event", rect),
        None => match query_tray_rect(app, scale) {
            Some(rect) => ("tray-rect", rect),
            None => {
                let (x, y) = platform::cursor_pos();
                ("cursor", (x, y, 0, 0))
            }
        },
    };
    let work = platform::work_area_for_point(ax + aw / 2, ay + ah / 2);
    let margin = (14.0 * scale).round() as i32;
    let w = size.width as i32;
    let h = size.height as i32;

    let mut x = ax + aw / 2 - w / 2;
    let above = ay - h - margin;
    let below = ay + ah + margin;
    let mut y = if above >= work.top {
        above
    } else if below + h <= work.bottom {
        below
    } else {
        work.bottom - h - margin
    };

    let min_x = work.left + margin;
    let max_x = (work.right - w - margin).max(min_x);
    let min_y = work.top + margin;
    let max_y = (work.bottom - h - margin).max(min_y);
    x = x.clamp(min_x, max_x);
    y = y.clamp(min_y, max_y);

    if let Err(e) = win.set_position(PhysicalPosition::new(x, y)) {
        crate::log_warn!("panel set_position 失败: {e}");
    }
    crate::log_info!(
        "panel position origin=({x},{y}) size={w}x{h} scale={scale:.3} work=({},{},{},{}) anchor_src={anchor_src} anchor=({ax},{ay},{aw}x{ah})",
        work.left,
        work.top,
        work.right,
        work.bottom
    );
}

/// 显示面板。顺序：恢复内存/还原最小化 → 定位 → show() → IsVisible(TRUE) → 事件 → 焦点。
pub fn show_panel(app: &AppHandle, reason: &str) {
    let state = app.state::<AppState>();
    state.bump_hide_generation();
    let Some(win) = app.get_webview_window("main") else {
        crate::log_error!("show_panel: main window 不存在");
        return;
    };
    // 恢复 WebView2 内存模式（TrySuspend 之后必须 Resume 才能正常出帧）
    let mem = platform::set_webview_memory_mode(&win, false);
    crate::log_info!("webview memory mode normal: {mem}");
    // 最小化还原：被 SW_MINIMIZE 的窗口直接 show() 仍是 iconic（origin=-32000）
    if win.is_minimized().unwrap_or(false) {
        crate::log_info!("window was minimized -> unminimize before show");
        if let Err(e) = win.unminimize() {
            crate::log_warn!("window.unminimize 失败: {e}");
        }
    }
    position_panel(app, &win);
    if let Err(e) = win.show() {
        crate::log_error!("window.show 失败: {e}");
    }
    let vis = platform::set_webview_visible(&win, true);
    crate::log_info!("window show reason={reason} webview IsVisible(true) -> {vis}");
    let _ = app.emit("window-visibility", json!({ "hidden": false }));
    // 长时间隐藏（TrySuspend）后，前端可能错过隐藏期间的 device/tick：
    // 主动让 collector 回一个全量 tick，保证打开面板 ≤200ms 内显示最新数据。
    let snap = state.sidecar.request_snapshot();
    crate::log_info!("window show reason={reason} requested collector snapshot={snap}");
    state.set_window_visible(true);
    if let Err(e) = win.set_focus() {
        crate::log_warn!("window.set_focus 失败: {e}");
    }
    // 托盘点击引起的显示：给失焦隐藏留出抑制窗口
    state.suppress_for_ms(450);
    crate::log_info!("window shown reason={reason}");
    log_window_metrics(&win, "shown");
    crate::fps::arm_after_show(app);
    state.arm_pb_hide(app);
}

/// 隐藏后延迟降 WebView2 内存（TrySuspend 需要前端先处理完 window-visibility）。
fn schedule_memory_suspend(app: &AppHandle) {
    let handle = app.clone();
    let _ = std::thread::Builder::new()
        .name("webview-suspend".into())
        .spawn(move || {
            std::thread::sleep(Duration::from_millis(300));
            let h = handle.clone();
            let _ = handle.run_on_main_thread(move || {
                let Some(win) = h.get_webview_window("main") else {
                    return;
                };
                let state = h.state::<AppState>();
                if state.window_visible() {
                    return; // 300ms 内又被显示，放弃 suspend
                }
                let r = platform::set_webview_memory_mode(&win, true);
                crate::log_info!("webview memory mode low (hidden): {r}");
            });
        });
}

/// 隐藏面板。顺序：IsVisible(FALSE) → 事件 → window.hide() → 延迟 TrySuspend。
pub fn hide_panel(app: &AppHandle, reason: &str, force: bool) {
    let state = app.state::<AppState>();
    state.bump_hide_generation();
    if !force && state.pinned() {
        crate::log_debug!("window hide skipped (pinned) reason={reason}");
        return;
    }
    let Some(win) = app.get_webview_window("main") else {
        return;
    };
    let vis = platform::set_webview_visible(&win, false);
    crate::log_info!("window hide reason={reason} webview IsVisible(false) -> {vis}");
    let _ = app.emit("window-visibility", json!({ "hidden": true }));
    if let Err(e) = win.hide() {
        crate::log_warn!("window.hide 失败: {e}");
    }
    state.set_window_visible(false);
    crate::log_info!("window hidden reason={reason}");
    schedule_memory_suspend(app);
}

pub fn toggle_panel(app: &AppHandle, source: &str) {
    let state = app.state::<AppState>();
    if state.window_visible() {
        hide_panel(app, source, true);
    } else {
        show_panel(app, source);
    }
}

/// ui_ready 且默认不显示时：让 WebView2 停止出帧（隐藏四步第 4 项）。
pub fn mark_startup_hidden(app: &AppHandle) {
    let state = app.state::<AppState>();
    state.bump_hide_generation();
    if let Some(win) = app.get_webview_window("main") {
        let vis = platform::set_webview_visible(&win, false);
        crate::log_info!("window startup hidden webview IsVisible(false) -> {vis}");
        let _ = app.emit("window-visibility", json!({ "hidden": true }));
        let _ = win.hide();
        log_window_metrics(&win, "startup-hidden");
    }
    state.set_window_visible(false);
    crate::log_info!("window hidden reason=startup-default");
    schedule_memory_suspend(app);
}

fn schedule_auto_hide(app: &AppHandle, delay_ms: u64) {
    let state = app.state::<AppState>();
    if state.pinned() || !state.window_visible() {
        return;
    }
    let generation = state.bump_hide_generation();
    let handle = app.clone();
    let _ = std::thread::Builder::new()
        .name("auto-hide".into())
        .spawn(move || {
            std::thread::sleep(Duration::from_millis(delay_ms));
            {
                let state = handle.state::<AppState>();
                if state.hide_generation() != generation {
                    crate::log_debug!("auto-hide cancelled (generation)");
                    return;
                }
                if state.pinned() || !state.window_visible() || state.is_suppressed() {
                    crate::log_debug!("auto-hide skipped (pinned/visible/suppressed)");
                    return;
                }
            }
            let h2 = handle.clone();
            let _ = handle.run_on_main_thread(move || {
                let state = h2.state::<AppState>();
                if state.hide_generation() != generation
                    || state.pinned()
                    || !state.window_visible()
                    || state.is_suppressed()
                {
                    return;
                }
                let ours = h2
                    .get_webview_window("main")
                    .and_then(|w| platform::window_hwnd(&w));
                let fg = platform::foreground_root();
                if let (Some(ours), Some(fg)) = (ours, fg) {
                    if ours == fg {
                        crate::log_debug!("auto-hide skipped: foreground is self");
                        return;
                    }
                }
                crate::log_info!("auto-hide triggered foreground={fg:?} ours={ours:?}");
                hide_panel(&h2, "focus-lost", false);
            });
        });
}

/// 窗口事件：失焦延迟隐藏、DPI 变化重设尺寸、关闭按钮 = 隐藏。
pub fn attach_window_events(app: &AppHandle) {
    let Some(win) = app.get_webview_window("main") else {
        crate::log_error!("attach_window_events: main window 不存在");
        return;
    };
    let handle = app.clone();
    win.on_window_event(move |event| match event {
        WindowEvent::CloseRequested { api, .. } => {
            api.prevent_close();
            crate::log_info!("window close requested -> hide");
            hide_panel(&handle, "close-requested", true);
        }
        WindowEvent::Focused(false) => {
            schedule_auto_hide(&handle, 270);
        }
        WindowEvent::Focused(true) => {
            let state = handle.state::<AppState>();
            state.bump_hide_generation();
        }
        WindowEvent::ScaleFactorChanged { scale_factor, .. } => {
            crate::log_info!("scaleFactorChanged scale={scale_factor:.3}");
            let state = handle.state::<AppState>();
            state.set_scale_factor(*scale_factor);
            if let Some(w) = handle.get_webview_window("main") {
                if let Err(e) = w.set_size(LogicalSize::new(780.0, 440.0)) {
                    crate::log_warn!("scaleFactorChanged: set_size 失败: {e}");
                }
                log_window_metrics(&w, "scale-changed");
            }
            crate::tray::update_tray_icon(&handle, "dpi");
            if state.window_visible() {
                if let Some(w) = handle.get_webview_window("main") {
                    position_panel(&handle, &w);
                }
            }
        }
        _ => {}
    });
}
